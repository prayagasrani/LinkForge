import uuid
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_access_token
from app.db.session import get_db_session
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/login")
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


async def get_current_user(
    session: SessionDep, token: Annotated[str, Depends(oauth2_scheme)]
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        user_id = uuid.UUID(decode_access_token(token))
    except (jwt.InvalidTokenError, ValueError):
        raise credentials_error from None
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise credentials_error
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def _client_identity(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _enforce_rate_limit(request: Request, scope: str, limit: int) -> None:
    result = await request.app.state.rate_limiter.check(scope, _client_identity(request), limit)
    if not result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(result.retry_after)},
        )


async def api_rate_limit(request: Request) -> None:
    await _enforce_rate_limit(request, "api", settings.api_rate_limit)


async def auth_rate_limit(request: Request) -> None:
    await _enforce_rate_limit(request, "auth", settings.auth_rate_limit)


async def redirect_rate_limit(request: Request) -> None:
    await _enforce_rate_limit(request, "redirect", settings.api_rate_limit)
