from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.dependencies import SessionDep, redirect_rate_limit
from app.models.short_url import ShortURL
from app.services.analytics import record_click_event
from app.services.cache import CachedLink

router = APIRouter(tags=["redirects"])


def _validate_access(link: CachedLink) -> None:
    if not link.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Short URL not found")
    if link.expires_at is not None and link.expires_at <= datetime.now(UTC):
        raise HTTPException(status.HTTP_410_GONE, "Short URL has expired")


@router.get("/{short_code}", dependencies=[Depends(redirect_rate_limit)])
async def redirect_to_original(
    short_code: str,
    request: Request,
    background_tasks: BackgroundTasks,
    session: SessionDep,
) -> RedirectResponse:
    cache = request.app.state.link_cache
    cached = await cache.get(short_code)
    if cached is not None:
        try:
            _validate_access(cached)
        except HTTPException:
            await cache.delete(short_code)
            raise
        link_data = cached
    else:
        db_link = await session.scalar(select(ShortURL).where(ShortURL.short_code == short_code))
        if db_link is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Short URL not found")
        link_data = CachedLink(
            id=db_link.id,
            original_url=db_link.original_url,
            is_active=db_link.is_active,
            expires_at=db_link.expires_at,
        )
        _validate_access(link_data)
        await cache.set(short_code, link_data)

    background_tasks.add_task(
        record_click_event,
        link_data.id,
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
        request.headers.get("referer"),
    )
    return RedirectResponse(
        str(link_data.original_url), status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )
