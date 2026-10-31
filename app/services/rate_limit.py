import math
from collections.abc import Callable
from dataclasses import dataclass
from time import time

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = structlog.get_logger()

INCREMENT_WITH_EXPIRY = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
end
return current
"""


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class RateLimiter:
    def __init__(
        self, redis: Redis, window_seconds: int, clock: Callable[[], float] = time
    ) -> None:
        self.redis = redis
        self.window_seconds = window_seconds
        self.clock = clock

    async def check(self, scope: str, identity: str, limit: int) -> RateLimitResult:
        now = self.clock()
        bucket = int(now // self.window_seconds)
        seconds_until_reset = (bucket + 1) * self.window_seconds - now
        ttl_milliseconds = max(math.ceil(seconds_until_reset * 1000), 1)
        retry_after = max(math.ceil(seconds_until_reset), 1)
        key = f"rate:{scope}:{identity}:{bucket}"
        try:
            count = int(await self.redis.eval(INCREMENT_WITH_EXPIRY, 1, key, ttl_milliseconds))
            return RateLimitResult(
                allowed=count <= limit,
                limit=limit,
                remaining=max(limit - count, 0),
                retry_after=retry_after,
            )
        except RedisError as exc:
            logger.warning("rate_limit_unavailable", scope=scope, error_type=type(exc).__name__)
            return RateLimitResult(True, limit, limit, 0)
