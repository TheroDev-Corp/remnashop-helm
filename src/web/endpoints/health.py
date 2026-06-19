from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Response, Security, status

from src.core.config import AppConfig
from src.infrastructure.services import HealthService
from src.infrastructure.services.health import DatabaseStatus, PoolMetrics
from src.web.dependencies import require_api_key
from src.web.schemas import (
    DatabaseStatusSchema,
    HealthChecks,
    HealthDetailsResponse,
    HealthResponse,
    PoolMetricsSchema,
)

router = APIRouter()


def _map_pool(pool: PoolMetrics) -> PoolMetricsSchema:
    return PoolMetricsSchema(
        size=pool.size,
        checked_in=pool.checked_in,
        checked_out=pool.checked_out,
        overflow=pool.overflow,
        total_connections=pool.total_connections,
        utilization=pool.utilization,
    )


def _map_database(db: DatabaseStatus) -> DatabaseStatusSchema:
    return DatabaseStatusSchema(
        ok=db.ok,
        latency_ms=db.latency_ms,
        pool=_map_pool(db.pool) if db.pool else None,
    )


@router.get("/health", response_model=HealthResponse)
@inject
async def health_check(
    response: Response,
    health_service: FromDishka[HealthService],
) -> HealthResponse:
    result = await health_service.check()

    if not result.ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(status="ok" if result.ok else "degraded")


@router.get("/health/details", response_model=HealthDetailsResponse)
@inject
async def health_check_details(
    response: Response,
    health_service: FromDishka[HealthService],
    config: FromDishka[AppConfig],
    _: None = Security(require_api_key),
) -> HealthDetailsResponse:
    result = await health_service.check()

    if not result.ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthDetailsResponse(
        status="ok" if result.ok else "degraded",
        version=config.build.tag,
        branch=config.build.branch,
        commit=config.build.commit,
        checks=HealthChecks(
            database=_map_database(result.database),
            redis=result.redis,
        ),
    )
