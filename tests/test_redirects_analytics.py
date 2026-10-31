from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from redis.exceptions import RedisError
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import AsyncSessionFactory
from app.main import app
from app.models.short_url import ShortURL
from app.services.cache import CachedLink, LinkCache


class UnavailableRedis:
    async def get(self, *args):  # type: ignore[no-untyped-def]
        raise RedisError("unavailable")

    async def setex(self, *args):  # type: ignore[no-untyped-def]
        raise RedisError("unavailable")

    async def delete(self, *args):  # type: ignore[no-untyped-def]
        raise RedisError("unavailable")


class FailingAnalyticsContext:
    async def __aenter__(self):  # type: ignore[no-untyped-def]
        raise SQLAlchemyError("simulated analytics outage")

    async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
        return None


async def test_redirect_cache_miss_then_hit_records_analytics(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/api/v1/urls",
        json={"original_url": "https://destination.example/path", "custom_alias": "analytics"},
        headers=auth_headers,
    )
    link_id = created.json()["id"]

    first = await client.get(
        "/analytics", headers={"User-Agent": "linkforge-test", "Referer": "https://ref.example"}
    )
    assert await app.state.redis.exists("link:analytics") == 1
    async with AsyncSessionFactory() as session:
        await session.execute(
            update(ShortURL)
            .where(ShortURL.id == link_id)
            .values(original_url="https://database-change.example")
        )
        await session.commit()
    second = await client.get("/analytics")
    assert first.status_code == second.status_code == 307
    assert first.headers["location"] == "https://destination.example/path"
    assert second.headers["location"] == "https://destination.example/path"

    analytics = await client.get(f"/api/v1/urls/{link_id}/analytics", headers=auth_headers)
    assert analytics.status_code == 200
    body = analytics.json()
    assert body["total_clicks"] == 2
    assert body["total_events"] == 2
    assert body["events"][0]["ip_address"] == "127.0.0.1"
    assert any(event["user_agent"] == "linkforge-test" for event in body["events"])


async def test_missing_inactive_and_expired_links_have_distinct_behavior(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    missing = await client.get("/does-not-exist")
    assert missing.status_code == 404

    inactive = await client.post(
        "/api/v1/urls",
        json={"original_url": "https://example.com", "custom_alias": "inactive-link"},
        headers=auth_headers,
    )
    await client.patch(
        f"/api/v1/urls/{inactive.json()['id']}",
        json={"is_active": False},
        headers=auth_headers,
    )
    assert (await client.get("/inactive-link")).status_code == 404

    expires_at = datetime.now(UTC) + timedelta(hours=1)
    expired = await client.post(
        "/api/v1/urls",
        json={
            "original_url": "https://example.com",
            "custom_alias": "expired-link",
            "expires_at": expires_at.isoformat(),
        },
        headers=auth_headers,
    )
    async with AsyncSessionFactory() as session:
        await session.execute(
            update(ShortURL)
            .where(ShortURL.id == expired.json()["id"])
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    await app.state.link_cache.delete("expired-link")
    assert (await client.get("/expired-link")).status_code == 410


async def test_analytics_are_owner_only(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    created = await client.post(
        "/api/v1/urls", json={"original_url": "https://example.com"}, headers=auth_headers
    )
    from tests.conftest import register_and_login

    other_headers = await register_and_login(client, "analytics-other@example.com")
    response = await client.get(
        f"/api/v1/urls/{created.json()['id']}/analytics", headers=other_headers
    )
    assert response.status_code == 404


async def test_corrupt_cache_value_is_evicted_and_reloaded_from_postgres(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.post(
        "/api/v1/urls",
        json={"original_url": "https://truth.example/path", "custom_alias": "corrupt-cache"},
        headers=auth_headers,
    )
    await app.state.redis.set("link:corrupt-cache", '{"original_url":"javascript:bad"}')

    response = await client.get("/corrupt-cache")
    assert response.status_code == 307
    assert response.headers["location"] == "https://truth.example/path"
    repaired = await app.state.redis.get("link:corrupt-cache")
    assert str(CachedLink.model_validate_json(repaired).original_url) == (
        "https://truth.example/path"
    )


async def test_redirect_and_mutations_survive_redis_outage(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/api/v1/urls",
        json={"original_url": "https://available.example", "custom_alias": "redis-down"},
        headers=auth_headers,
    )
    real_cache = app.state.link_cache
    app.state.link_cache = LinkCache(UnavailableRedis(), 60)  # type: ignore[arg-type]
    try:
        redirected = await client.get("/redis-down")
        updated = await client.patch(
            f"/api/v1/urls/{created.json()['id']}",
            json={"original_url": "https://still-available.example"},
            headers=auth_headers,
        )
        deleted = await client.delete(f"/api/v1/urls/{created.json()['id']}", headers=auth_headers)
    finally:
        app.state.link_cache = real_cache

    assert redirected.status_code == 307
    assert updated.status_code == 200
    assert deleted.status_code == 204


async def test_analytics_failure_does_not_break_redirect(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    await client.post(
        "/api/v1/urls",
        json={"original_url": "https://example.com", "custom_alias": "analytics-down"},
        headers=auth_headers,
    )
    monkeypatch.setattr(
        "app.services.analytics.AsyncSessionFactory", lambda: FailingAnalyticsContext()
    )
    response = await client.get("/analytics-down")
    assert response.status_code == 307


async def test_analytics_truncates_oversized_request_metadata(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/api/v1/urls",
        json={"original_url": "https://example.com", "custom_alias": "large-metadata"},
        headers=auth_headers,
    )
    response = await client.get(
        "/large-metadata",
        headers={"User-Agent": "u" * 2_000, "Referer": "https://ref.example/" + "r" * 4_000},
    )
    assert response.status_code == 307
    analytics = await client.get(
        f"/api/v1/urls/{created.json()['id']}/analytics", headers=auth_headers
    )
    event = analytics.json()["events"][0]
    assert len(event["user_agent"]) == 512
    assert len(event["referrer"]) == 2048
