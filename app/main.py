import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import analytics, auth, health, redirects, urls
from app.core.config import settings
from app.core.logging import configure_logging
from app.services.cache import LinkCache
from app.services.rate_limit import RateLimiter

configure_logging(settings.log_level)
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_socket_timeout_seconds,
        socket_timeout=settings.redis_socket_timeout_seconds,
    )
    app.state.redis = redis
    app.state.link_cache = LinkCache(redis, settings.cache_ttl_seconds)
    app.state.rate_limiter = RateLimiter(redis, settings.rate_limit_window_seconds)
    logger.info("application_started", environment=settings.app_env)
    yield
    await redis.aclose()
    logger.info("application_stopped")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Authenticated URL shortening with Redis caching and click analytics.",
    lifespan=lifespan,
)


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.error(
        "database_operation_failed",
        path=request.url.path,
        error_type=type(exc).__name__,
    )
    return JSONResponse(
        status_code=503,
        content={"detail": "Database operation temporarily unavailable"},
    )


@app.middleware("http")
async def request_logging(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    started = time.perf_counter()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    try:
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        structlog.contextvars.clear_contextvars()


app.include_router(health.router)
app.include_router(auth.router, prefix=settings.api_v1_prefix)
app.include_router(urls.router, prefix=settings.api_v1_prefix)
app.include_router(analytics.router, prefix=settings.api_v1_prefix)
# The catch-all short-code route must remain last.
app.include_router(redirects.router)
