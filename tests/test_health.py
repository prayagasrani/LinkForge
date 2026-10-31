import uuid
from unittest.mock import AsyncMock

from httpx import AsyncClient
from sqlalchemy.exc import SQLAlchemyError

from app.core.security import create_access_token
from app.db.session import get_db_session
from app.main import app


async def test_liveness_and_readiness(client: AsyncClient) -> None:
    health = await client.get("/health")
    ready = await client.get("/ready")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "dependencies": {"database": "ok", "redis": "ok"},
    }


async def test_redis_outage_is_reported_as_degraded_but_still_ready(
    client: AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(app.state.link_cache, "ping", AsyncMock(return_value=False))
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {"database": "ok", "redis": "unavailable"},
    }


async def test_postgres_outage_fails_readiness_but_not_liveness(client: AsyncClient) -> None:
    class FailingSession:
        async def execute(self, *args):  # type: ignore[no-untyped-def]
            raise SQLAlchemyError("simulated database outage")

    async def failing_session():  # type: ignore[no-untyped-def]
        yield FailingSession()

    app.dependency_overrides[get_db_session] = failing_session
    try:
        ready = await client.get("/ready")
        health = await client.get("/health")
    finally:
        app.dependency_overrides.pop(get_db_session)

    assert ready.status_code == 503
    assert ready.json() == {
        "status": "not_ready",
        "dependencies": {"database": "unavailable", "redis": "ok"},
    }
    assert health.status_code == 200


async def test_unhandled_database_error_uses_sanitized_503_response(client: AsyncClient) -> None:
    class FailingSession:
        async def scalar(self, *args):  # type: ignore[no-untyped-def]
            raise SQLAlchemyError("sensitive database detail")

    async def failing_session():  # type: ignore[no-untyped-def]
        yield FailingSession()

    app.dependency_overrides[get_db_session] = failing_session
    token = create_access_token(str(uuid.uuid4()))
    try:
        response = await client.get("/api/v1/urls", headers={"Authorization": f"Bearer {token}"})
    finally:
        app.dependency_overrides.pop(get_db_session)

    assert response.status_code == 503
    assert response.json() == {"detail": "Database operation temporarily unavailable"}
    assert "sensitive" not in response.text
