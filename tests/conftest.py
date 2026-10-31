import os
from collections.abc import AsyncIterator

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://linkforge:linkforge@localhost:5432/linkforge_test"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("JWT_SECRET", "test-only-secret-with-more-than-32-characters")
os.environ.setdefault("AUTH_RATE_LIMIT", "100")
os.environ.setdefault("API_RATE_LIMIT", "1000")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.db.base import Base
from app.db.session import AsyncSessionFactory, engine
from app.main import app, lifespan
from app.models.click_event import ClickEvent
from app.models.short_url import ShortURL
from app.models.user import User


@pytest_asyncio.fixture(scope="session", autouse=True)
async def application_lifecycle() -> AsyncIterator[None]:
    database_name = make_url(settings.database_url).database or ""
    reset_allowed = os.getenv("ALLOW_TEST_DATABASE_RESET") == "true"
    if not database_name.endswith("_test") and not reset_allowed:
        raise RuntimeError(
            "Tests require a *_test database or ALLOW_TEST_DATABASE_RESET=true for ephemeral CI"
        )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with lifespan(app):
        yield
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_state(application_lifecycle: None) -> AsyncIterator[None]:
    async with AsyncSessionFactory() as session:
        await session.execute(delete(ClickEvent))
        await session.execute(delete(ShortURL))
        await session.execute(delete(User))
        await session.commit()
    await app.state.redis.flushdb()
    yield


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as test_client:
        yield test_client


async def register_and_login(
    client: AsyncClient, email: str = "owner@example.com"
) -> dict[str, str]:
    password = "correct-horse-battery-staple"
    registration = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert registration.status_code == 201, registration.text
    login = await client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    return await register_and_login(client)
