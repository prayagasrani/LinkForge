from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from app.main import app
from tests.conftest import register_and_login


async def test_create_custom_alias_and_reject_duplicate(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    payload = {"original_url": "https://example.com/articles/one", "custom_alias": "portfolio"}
    created = await client.post("/api/v1/urls", json=payload, headers=auth_headers)
    assert created.status_code == 201
    assert created.json()["short_code"] == "portfolio"
    assert created.json()["short_url"].endswith("/portfolio")

    duplicate = await client.post("/api/v1/urls", json=payload, headers=auth_headers)
    assert duplicate.status_code == 409


async def test_generated_code_retries_after_collision(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    codes = iter(["DUPL123", "DUPL123", "UNIQUE1"])
    monkeypatch.setattr("app.services.links.generate_short_code", lambda: next(codes))
    first = await client.post(
        "/api/v1/urls", json={"original_url": "https://one.example"}, headers=auth_headers
    )
    second = await client.post(
        "/api/v1/urls", json={"original_url": "https://two.example"}, headers=auth_headers
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["short_code"] == "DUPL123"
    assert second.json()["short_code"] == "UNIQUE1"


async def test_list_update_and_delete_url(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    for alias in ("first-link", "second-link"):
        response = await client.post(
            "/api/v1/urls",
            json={"original_url": "https://example.com", "custom_alias": alias},
            headers=auth_headers,
        )
        assert response.status_code == 201

    listing = await client.get(
        "/api/v1/urls?page=1&page_size=1&sort_order=asc", headers=auth_headers
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 2
    assert len(listing.json()["items"]) == 1

    link_id = listing.json()["items"][0]["id"]
    updated = await client.patch(
        f"/api/v1/urls/{link_id}",
        json={"original_url": "https://updated.example/path", "is_active": False},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["is_active"] is False
    assert updated.json()["original_url"] == "https://updated.example/path"

    deleted = await client.delete(f"/api/v1/urls/{link_id}", headers=auth_headers)
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/urls/{link_id}", headers=auth_headers)).status_code == 404


async def test_ownership_is_not_leaked(client: AsyncClient) -> None:
    owner_headers = await register_and_login(client, "owner@example.com")
    created = await client.post(
        "/api/v1/urls",
        json={"original_url": "https://private.example", "custom_alias": "owners-link"},
        headers=owner_headers,
    )
    other_headers = await register_and_login(client, "other@example.com")
    link_id = created.json()["id"]

    assert (await client.get(f"/api/v1/urls/{link_id}", headers=other_headers)).status_code == 404
    assert (
        await client.patch(
            f"/api/v1/urls/{link_id}", json={"is_active": False}, headers=other_headers
        )
    ).status_code == 404
    assert (
        await client.delete(f"/api/v1/urls/{link_id}", headers=other_headers)
    ).status_code == 404


async def test_invalid_url_alias_and_expiration_are_rejected(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    invalid_url = await client.post(
        "/api/v1/urls", json={"original_url": "not a URL"}, headers=auth_headers
    )
    invalid_alias = await client.post(
        "/api/v1/urls",
        json={"original_url": "https://example.com", "custom_alias": "bad alias!"},
        headers=auth_headers,
    )
    past_expiry = await client.post(
        "/api/v1/urls",
        json={
            "original_url": "https://example.com",
            "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        },
        headers=auth_headers,
    )
    assert invalid_url.status_code == invalid_alias.status_code == past_expiry.status_code == 422


async def test_update_invalidates_redirect_cache(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/api/v1/urls",
        json={"original_url": "https://old.example", "custom_alias": "cached-link"},
        headers=auth_headers,
    )
    assert (await client.get("/cached-link")).status_code == 307
    assert await app.state.redis.exists("link:cached-link") == 1

    updated = await client.patch(
        f"/api/v1/urls/{created.json()['id']}",
        json={"original_url": "https://new.example"},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert await app.state.redis.exists("link:cached-link") == 0
    redirected = await client.get("/cached-link")
    assert redirected.headers["location"] == "https://new.example/"


async def test_delete_invalidates_cache_and_removes_redirect(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/api/v1/urls",
        json={"original_url": "https://example.com", "custom_alias": "delete-cached"},
        headers=auth_headers,
    )
    await client.get("/delete-cached")
    assert await app.state.redis.exists("link:delete-cached") == 1

    deleted = await client.delete(f"/api/v1/urls/{created.json()['id']}", headers=auth_headers)
    assert deleted.status_code == 204
    assert await app.state.redis.exists("link:delete-cached") == 0
    assert (await client.get("/delete-cached")).status_code == 404


async def test_expiration_can_be_set_changed_and_cleared(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/api/v1/urls", json={"original_url": "https://example.com"}, headers=auth_headers
    )
    link_id = created.json()["id"]
    first_expiry = datetime.now(UTC) + timedelta(hours=1)
    second_expiry = datetime.now(UTC) + timedelta(days=1)

    first = await client.patch(
        f"/api/v1/urls/{link_id}",
        json={"expires_at": first_expiry.isoformat()},
        headers=auth_headers,
    )
    second = await client.patch(
        f"/api/v1/urls/{link_id}",
        json={"expires_at": second_expiry.isoformat()},
        headers=auth_headers,
    )
    cleared = await client.patch(
        f"/api/v1/urls/{link_id}", json={"expires_at": None}, headers=auth_headers
    )
    assert first.status_code == second.status_code == cleared.status_code == 200
    assert first.json()["expires_at"] is not None
    assert second.json()["expires_at"] != first.json()["expires_at"]
    assert cleared.json()["expires_at"] is None


async def test_alias_boundaries_and_reserved_routes(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    minimum = await client.post(
        "/api/v1/urls",
        json={"original_url": "https://example.com", "custom_alias": "abc"},
        headers=auth_headers,
    )
    maximum_alias = "a" * 32
    maximum = await client.post(
        "/api/v1/urls",
        json={"original_url": "https://example.com", "custom_alias": maximum_alias},
        headers=auth_headers,
    )
    too_short = await client.post(
        "/api/v1/urls",
        json={"original_url": "https://example.com", "custom_alias": "ab"},
        headers=auth_headers,
    )
    too_long = await client.post(
        "/api/v1/urls",
        json={"original_url": "https://example.com", "custom_alias": "a" * 33},
        headers=auth_headers,
    )
    reserved = await client.post(
        "/api/v1/urls",
        json={"original_url": "https://example.com", "custom_alias": "DoCs"},
        headers=auth_headers,
    )
    assert minimum.status_code == maximum.status_code == 201
    assert too_short.status_code == too_long.status_code == 422
    assert reserved.status_code == 409


async def test_link_listing_only_contains_authenticated_users_links(client: AsyncClient) -> None:
    owner_headers = await register_and_login(client, "listing-owner@example.com")
    other_headers = await register_and_login(client, "listing-other@example.com")
    await client.post(
        "/api/v1/urls",
        json={"original_url": "https://owner.example", "custom_alias": "owner-visible"},
        headers=owner_headers,
    )
    await client.post(
        "/api/v1/urls",
        json={"original_url": "https://other.example", "custom_alias": "other-hidden"},
        headers=other_headers,
    )

    listing = await client.get("/api/v1/urls", headers=owner_headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["short_code"] == "owner-visible"
