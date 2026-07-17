from typing import Optional

from pydantic import BaseModel


class PublicPlanLandingResponse(BaseModel):
    public_code: str
    name: str
    description: Optional[str] = None
    traffic_limit: int
    device_limit: int
    monthly_from_rub: str
    max_duration_days: int
    max_duration_price_rub: str


class PublicPlanLandingListResponse(BaseModel):
    plans: list[PublicPlanLandingResponse]
