import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select

from app.api.dependencies import CurrentUser, SessionDep, api_rate_limit
from app.models.click_event import ClickEvent
from app.schemas.analytics import AnalyticsResponse, ClickEventResponse
from app.services.links import get_owned_link

router = APIRouter(prefix="/urls", tags=["analytics"], dependencies=[Depends(api_rate_limit)])


@router.get("/{link_id}/analytics", response_model=AnalyticsResponse)
async def get_analytics(
    link_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> AnalyticsResponse:
    link = await get_owned_link(session, link_id, user.id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "URL not found")

    total_events = await session.scalar(
        select(func.count(ClickEvent.id)).where(ClickEvent.short_url_id == link.id)
    )
    events = await session.scalars(
        select(ClickEvent)
        .where(ClickEvent.short_url_id == link.id)
        .order_by(desc(ClickEvent.clicked_at), ClickEvent.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return AnalyticsResponse(
        short_url_id=link.id,
        short_code=link.short_code,
        total_clicks=link.click_count,
        events=[ClickEventResponse.model_validate(event) for event in events.all()],
        page=page,
        page_size=page_size,
        total_events=total_events or 0,
    )
