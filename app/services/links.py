import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.short_url import ShortURL
from app.schemas.short_url import ShortURLCreate
from app.services.short_codes import generate_short_code

RESERVED_CODES = {"api", "docs", "redoc", "openapi.json", "health", "ready"}
MAX_GENERATION_ATTEMPTS = 10
SHORT_CODE_UNIQUE_CONSTRAINT = "ix_short_urls_short_code"


class AliasAlreadyExistsError(Exception):
    pass


class ShortCodeGenerationError(Exception):
    pass


def _is_short_code_unique_violation(error: IntegrityError) -> bool:
    """Inspect the driver error chain without relying on database error text."""
    current: BaseException | None = error.orig
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if (
            getattr(current, "sqlstate", None) == "23505"
            and getattr(current, "constraint_name", None) == SHORT_CODE_UNIQUE_CONSTRAINT
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


async def create_short_url(
    session: AsyncSession, owner_id: uuid.UUID, data: ShortURLCreate
) -> ShortURL:
    custom_alias = data.custom_alias
    if custom_alias and custom_alias.lower() in RESERVED_CODES:
        raise AliasAlreadyExistsError

    attempts = 1 if custom_alias else MAX_GENERATION_ATTEMPTS
    for _ in range(attempts):
        short_code = custom_alias or generate_short_code()
        link = ShortURL(
            owner_id=owner_id,
            short_code=short_code,
            original_url=str(data.original_url),
            expires_at=data.expires_at,
        )
        try:
            async with session.begin_nested():
                session.add(link)
                await session.flush()
            return link
        except IntegrityError as exc:
            if not _is_short_code_unique_violation(exc):
                raise
            if custom_alias:
                raise AliasAlreadyExistsError from None

    raise ShortCodeGenerationError


async def get_owned_link(
    session: AsyncSession, link_id: uuid.UUID, owner_id: uuid.UUID
) -> ShortURL | None:
    result = await session.execute(
        select(ShortURL).where(ShortURL.id == link_id, ShortURL.owner_id == owner_id)
    )
    return result.scalar_one_or_none()
