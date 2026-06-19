from decimal import Decimal

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter
from redis.asyncio import Redis

from src.application.common.dao import PlanDao
from src.core.constants import PUBLIC_LANDING_PLANS_CACHE_TTL_SECONDS
from src.core.enums import Currency, PlanAvailability
from src.web.schemas import PublicPlanLandingListResponse, PublicPlanLandingResponse

from ._common import _normalize_decimal_str

router = APIRouter(tags=["Public - Plans"])

_CACHE_KEY = "cache:public_landing_plans"


@router.get("/plans/public", response_model=PublicPlanLandingListResponse)
@inject
async def get_public_landing_plans(
    plan_dao: FromDishka[PlanDao],
    redis: FromDishka[Redis],
) -> PublicPlanLandingListResponse:
    cached = await redis.get(_CACHE_KEY)
    if cached is not None:
        return PublicPlanLandingListResponse.model_validate_json(cached)

    plans = await plan_dao.filter_by_availability(PlanAvailability.ALL)

    result: list[PublicPlanLandingResponse] = []
    for plan in plans:
        if not plan.is_active or plan.is_trial or not plan.public_code:
            continue

        rub_duration_candidates: list[tuple[int, Decimal]] = []
        for duration in plan.durations:
            if duration.days <= 0:
                continue

            rub_price = next((p.price for p in duration.prices if p.currency == Currency.RUB), None)
            if rub_price is not None:
                rub_duration_candidates.append((duration.days, rub_price))

        if not rub_duration_candidates:
            continue

        max_duration_days, max_duration_price = max(
            rub_duration_candidates,
            key=lambda item: item[0],
        )
        monthly_from = (max_duration_price * Decimal(30)) / Decimal(max_duration_days)

        result.append(
            PublicPlanLandingResponse(
                public_code=plan.public_code,
                name=plan.name,
                description=plan.description,
                traffic_limit=plan.traffic_limit,
                device_limit=plan.device_limit,
                monthly_from_rub=_normalize_decimal_str(monthly_from),
                max_duration_days=max_duration_days,
                max_duration_price_rub=_normalize_decimal_str(max_duration_price),
            )
        )

    payload = PublicPlanLandingListResponse(plans=result)
    await redis.setex(_CACHE_KEY, PUBLIC_LANDING_PLANS_CACHE_TTL_SECONDS, payload.model_dump_json())
    return payload
