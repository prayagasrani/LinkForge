import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.session import AsyncSessionFactory
from app.schemas.short_url import ShortURLCreate
from app.services.links import create_short_url


async def test_unrelated_integrity_error_is_not_treated_as_code_collision() -> None:
    data = ShortURLCreate(original_url="https://example.com")
    async with AsyncSessionFactory() as session:
        with pytest.raises(IntegrityError):
            await create_short_url(session, uuid.uuid4(), data)
        await session.rollback()
