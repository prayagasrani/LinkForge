import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import asc, desc, func, or_, select

from app.api.dependencies import CurrentUser, SessionDep, api_rate_limit
from app.core.config import settings
from app.models.short_url import ShortURL
from app.schemas.short_url import (
    ShortURLCreate,
    ShortURLListResponse,
    ShortURLResponse,
    ShortURLUpdate,
)
from app.services.links import (
    AliasAlreadyExistsError,
    ShortCodeGenerationError,
    create_short_url,
    get_owned_link,
)

router = APIRouter(prefix="/urls", tags=["URLs"], dependencies=[Depends(api_rate_limit)])


def to_response(link: ShortURL) -> ShortURLResponse:
    base_url = settings.public_base_url.rstrip("/")
    return ShortURLResponse(
        id=link.id,
        owner_id=link.owner_id,
        short_code=link.short_code,
        short_url=f"{base_url}/{link.short_code}",
        original_url=link.original_url,
        is_active=link.is_active,
        expires_at=link.expires_at,
        click_count=link.click_count,
        created_at=link.created_at,
        updated_at=link.updated_at,
    )


@router.post("", response_model=ShortURLResponse, status_code=status.HTTP_201_CREATED)
async def create_url(
    data: ShortURLCreate, user: CurrentUser, session: SessionDep
) -> ShortURLResponse:
    try:
        link = await create_short_url(session, user.id, data)
    except AliasAlreadyExistsError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Custom alias is already in use") from None
    except ShortCodeGenerationError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not allocate a short code; retry the request",
        ) from None
    await session.commit()
    await session.refresh(link)
    return to_response(link)


@router.get("", response_model=ShortURLListResponse)
async def list_urls(
    user: CurrentUser,
    session: SessionDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    is_active: bool | None = None,
    expired: bool | None = None,
    sort_by: Literal["created_at", "click_count"] = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
) -> ShortURLListResponse:
    predicates = [ShortURL.owner_id == user.id]
    if is_active is not None:
        predicates.append(ShortURL.is_active == is_active)
    now = datetime.now(UTC)
    if expired is True:
        predicates.append(ShortURL.expires_at <= now)
    elif expired is False:
        predicates.append(or_(ShortURL.expires_at.is_(None), ShortURL.expires_at > now))

    total = await session.scalar(select(func.count(ShortURL.id)).where(*predicates))
    sort_column = ShortURL.created_at if sort_by == "created_at" else ShortURL.click_count
    ordering = asc(sort_column) if sort_order == "asc" else desc(sort_column)
    result = await session.scalars(
        select(ShortURL)
        .where(*predicates)
        .order_by(ordering, ShortURL.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return ShortURLListResponse(
        items=[to_response(link) for link in result.all()],
        page=page,
        page_size=page_size,
        total=total or 0,
    )


@router.get("/{link_id}", response_model=ShortURLResponse)
async def get_url(link_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> ShortURLResponse:
    link = await get_owned_link(session, link_id, user.id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "URL not found")
    return to_response(link)


@router.patch("/{link_id}", response_model=ShortURLResponse)
async def update_url(
    link_id: uuid.UUID,
    data: ShortURLUpdate,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> ShortURLResponse:
    link = await get_owned_link(session, link_id, user.id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "URL not found")

    if "original_url" in data.model_fields_set:
        link.original_url = str(data.original_url)
    if "is_active" in data.model_fields_set:
        link.is_active = bool(data.is_active)
    if "expires_at" in data.model_fields_set:
        link.expires_at = data.expires_at
    await session.commit()
    await session.refresh(link)
    await request.app.state.link_cache.delete(link.short_code)
    return to_response(link)


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_url(
    link_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> Response:
    link = await get_owned_link(session, link_id, user.id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "URL not found")
    short_code = link.short_code
    await session.delete(link)
    await session.commit()
    await request.app.state.link_cache.delete(short_code)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
