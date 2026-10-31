import uuid
from datetime import UTC, datetime, timedelta

import jwt
from httpx import AsyncClient
from sqlalchemy import select

from app.api.routes import auth as auth_routes
from app.core.config import settings
from app.core.security import DUMMY_PASSWORD_HASH
from app.db.session import AsyncSessionFactory
from app.models.user import User


async def test_register_hashes_password_and_login_returns_token(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "Student@Example.com", "password": "a-secure-password"},
    )
    assert response.status_code == 201
    assert response.json()["email"] == "student@example.com"

    async with AsyncSessionFactory() as session:
        user = await session.scalar(select(User).where(User.email == "student@example.com"))
        assert user is not None
        assert user.password_hash != "a-secure-password"

    login = await client.post(
        "/api/v1/auth/login",
        data={"username": "STUDENT@example.com", "password": "a-secure-password"},
    )
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"
    assert login.json()["access_token"]


async def test_duplicate_registration_and_bad_login_are_rejected(client: AsyncClient) -> None:
    payload = {"email": "duplicate@example.com", "password": "a-secure-password"}
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 201
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 409
    bad_login = await client.post(
        "/api/v1/auth/login", data={"username": payload["email"], "password": "wrong-password"}
    )
    assert bad_login.status_code == 401


async def test_invalid_token_cannot_access_protected_route(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/urls", headers={"Authorization": "Bearer definitely-not-a-jwt"}
    )
    assert response.status_code == 401


async def test_nonexistent_login_executes_dummy_argon2_verification(
    client: AsyncClient, monkeypatch
) -> None:
    verified_hashes: list[str] = []

    def record_verification(password: str, hashed_password: str) -> bool:
        verified_hashes.append(hashed_password)
        return False

    monkeypatch.setattr(auth_routes, "verify_password", record_verification)
    response = await client.post(
        "/api/v1/auth/login",
        data={"username": "missing@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Incorrect email or password"}
    assert verified_hashes == [DUMMY_PASSWORD_HASH]


async def test_failed_login_does_not_reveal_account_existence(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"email": "known@example.com", "password": "a-secure-password"},
    )
    known = await client.post(
        "/api/v1/auth/login",
        data={"username": "known@example.com", "password": "wrong-password"},
    )
    unknown = await client.post(
        "/api/v1/auth/login",
        data={"username": "unknown@example.com", "password": "wrong-password"},
    )
    assert known.status_code == unknown.status_code == 401
    assert known.json() == unknown.json() == {"detail": "Incorrect email or password"}


def _signed_token(payload: dict[str, object], secret: str | None = None) -> str:
    return jwt.encode(
        payload,
        secret or settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


async def test_invalid_jwt_variants_have_the_same_401_contract(client: AsyncClient) -> None:
    now = datetime.now(UTC)
    subject = str(uuid.uuid4())
    valid_claims: dict[str, object] = {
        "sub": subject,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    variants = [
        "not-a-jwt",
        _signed_token(valid_claims, "a-different-signing-secret-with-32-characters"),
        _signed_token({**valid_claims, "exp": now - timedelta(seconds=1)}),
        _signed_token({**valid_claims, "type": "refresh"}),
        _signed_token({key: value for key, value in valid_claims.items() if key != "exp"}),
        _signed_token({key: value for key, value in valid_claims.items() if key != "sub"}),
        _signed_token({**valid_claims, "sub": "not-a-uuid"}),
    ]

    for token in variants:
        response = await client.get("/api/v1/urls", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.json() == {"detail": "Could not validate credentials"}
        assert response.headers["www-authenticate"] == "Bearer"


async def test_missing_bearer_token_is_unauthorized(client: AsyncClient) -> None:
    response = await client.get("/api/v1/urls")
    assert response.status_code == 401
    assert "token" not in response.text.lower()
