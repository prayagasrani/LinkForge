from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import SessionDep
from app.schemas.health import DependencyHealth, HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def ready(request: Request, session: SessionDep) -> ReadinessResponse | JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
        database_status = "ok"
    except SQLAlchemyError:
        database_status = "unavailable"
    redis_status = "ok" if await request.app.state.link_cache.ping() else "unavailable"
    response = ReadinessResponse(
        status="ready" if database_status == "ok" else "not_ready",
        dependencies=DependencyHealth(database=database_status, redis=redis_status),
    )
    if database_status != "ok":
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response.model_dump(),
        )
    return response
