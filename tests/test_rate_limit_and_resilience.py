from httpx import AsyncClient
from redis.exceptions import RedisError

from app.core.config import settings
from app.services.cache import CachedLink, LinkCache
from app.services.rate_limit import RateLimiter


class UnavailableRedis:
    async def get(self, *args):  # type: ignore[no-untyped-def]
        raise RedisError("unavailable")

    async def setex(self, *args):  # type: ignore[no-untyped-def]
        raise RedisError("unavailable")

    async def delete(self, *args):  # type: ignore[no-untyped-def]
        raise RedisError("unavailable")

    async def ping(self, *args):  # type: ignore[no-untyped-def]
        raise RedisError("unavailable")

    async def eval(self, *args):  # type: ignore[no-untyped-def]
        raise RedisError("unavailable")


class CountingRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.calls: list[tuple[str, int]] = []

    async def eval(self, script: str, key_count: int, key: str, ttl_ms: int) -> int:
        assert "PEXPIRE" in script
        assert key_count == 1
        self.calls.append((key, ttl_ms))
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


class InvalidEncodingRedis:
    def __init__(self) -> None:
        self.deleted_keys: list[str] = []

    async def get(self, key: str) -> str:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    async def delete(self, key: str) -> None:
        self.deleted_keys.append(key)


async def test_auth_rate_limit_returns_429(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_rate_limit", 2)
    responses = [
        await client.post(
            "/api/v1/auth/login", data={"username": "nobody@example.com", "password": "wrong"}
        )
        for _ in range(3)
    ]
    assert [response.status_code for response in responses] == [401, 401, 429]
    assert int(responses[-1].headers["retry-after"]) > 0


async def test_redis_failure_is_fail_open() -> None:
    unavailable = UnavailableRedis()
    cache = LinkCache(unavailable, default_ttl=60)  # type: ignore[arg-type]
    assert await cache.get("anything") is None
    await cache.set(
        "anything",
        CachedLink(
            id="2fc27871-5426-4af6-9068-dd8c6605d972",  # type: ignore[arg-type]
            original_url="https://example.com",
            is_active=True,
            expires_at=None,
        ),
    )
    await cache.delete("anything")
    assert await cache.ping() is False

    result = await RateLimiter(unavailable, 60).check("api", "client", 10)  # type: ignore[arg-type]
    assert result.allowed is True
    assert result.remaining == 10


async def test_rate_limit_resets_and_reports_time_at_epoch_boundary() -> None:
    redis = CountingRedis()
    now = [59.75]
    limiter = RateLimiter(redis, 60, clock=lambda: now[0])  # type: ignore[arg-type]

    first = await limiter.check("api", "client", limit=1)
    blocked = await limiter.check("api", "client", limit=1)
    assert first.allowed is True
    assert blocked.allowed is False
    assert blocked.retry_after == 1
    assert redis.calls[-1] == ("rate:api:client:0", 250)

    now[0] = 60.0
    reset = await limiter.check("api", "client", limit=1)
    assert reset.allowed is True
    assert reset.remaining == 0
    assert reset.retry_after == 60
    assert redis.calls[-1] == ("rate:api:client:1", 60_000)


async def test_invalid_cache_encoding_is_evicted() -> None:
    redis = InvalidEncodingRedis()
    cache = LinkCache(redis, 60)  # type: ignore[arg-type]
    assert await cache.get("invalid-encoding") is None
    assert redis.deleted_keys == ["link:invalid-encoding"]
