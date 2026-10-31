import uuid

import structlog
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import AsyncSessionFactory
from app.models.click_event import ClickEvent
from app.models.short_url import ShortURL

logger = structlog.get_logger()


def _bounded(value: str | None, maximum: int) -> str | None:
    return value[:maximum] if value else None


async def record_click_event(
    short_url_id: uuid.UUID,
    ip_address: str | None,
    user_agent: str | None,
    referrer: str | None,
) -> None:
    """Best-effort in-process analytics write executed after the redirect response."""
    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                update(ShortURL)
                .where(ShortURL.id == short_url_id)
                .values(
                    click_count=ShortURL.click_count + 1,
                    # Analytics activity should not change the link-settings timestamp.
                    updated_at=ShortURL.updated_at,
                )
            )
            if result.rowcount == 0:  # type: ignore[attr-defined]
                return
            session.add(
                ClickEvent(
                    short_url_id=short_url_id,
                    ip_address=_bounded(ip_address, 45),
                    user_agent=_bounded(user_agent, 512),
                    referrer=_bounded(referrer, 2048),
                )
            )
            await session.commit()
    except SQLAlchemyError as exc:
        logger.error(
            "analytics_write_failed",
            short_url_id=str(short_url_id),
            error_type=type(exc).__name__,
        )
