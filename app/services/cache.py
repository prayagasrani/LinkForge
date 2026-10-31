import math
import uuid
from datetime import UTC, datetime

import structlog
from pydantic import BaseModel, HttpUrl, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = structlog.get_logger()


class CachedLink(BaseModel):
    id: uuid.UUID
    original_url: HttpUrl
    is_active: bool
    expires_at: datetime | None


class LinkCache:
    def __init__(self, redis: Redis, default_ttl: int) -> None:
        self.redis = redis
        self.default_ttl = default_ttl

    @staticmethod
    def _key(short_code: str) -> str:
        return f"link:{short_code}"

    async def get(self, short_code: str) -> CachedLink | None:
        try:
            raw = await self.redis.get(self._key(short_code))
        except UnicodeError as exc:
            logger.warning(
                "cache_value_invalid", short_code=short_code, error_type=type(exc).__name__
            )
            await self.delete(short_code)
            return None
        except RedisError as exc:
            logger.warning(
                "cache_read_failed", short_code=short_code, error_type=type(exc).__name__
            )
            return None
        if not raw:
            return None
        try:
            return CachedLink.model_validate_json(raw)
        except (ValidationError, ValueError) as exc:
            logger.warning(
                "cache_value_invalid", short_code=short_code, error_type=type(exc).__name__
            )
            await self.delete(short_code)
            return None

    async def set(self, short_code: str, link: CachedLink) -> None:
        ttl = self.default_ttl
        if link.expires_at is not None:
            remaining = math.ceil((link.expires_at - datetime.now(UTC)).total_seconds())
            ttl = min(ttl, remaining)
        if ttl <= 0:
            return
        try:
            await self.redis.setex(self._key(short_code), ttl, link.model_dump_json())
        except RedisError as exc:
            logger.warning(
                "cache_write_failed", short_code=short_code, error_type=type(exc).__name__
            )

    async def delete(self, short_code: str) -> None:
        try:
            await self.redis.delete(self._key(short_code))
        except RedisError as exc:
            logger.warning(
                "cache_delete_failed", short_code=short_code, error_type=type(exc).__name__
            )

    async def ping(self) -> bool:
        try:
            return bool(await self.redis.ping())
        except RedisError:
            return False
