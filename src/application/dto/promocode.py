from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from src.core.enums import PromocodeAvailability, PromocodeRewardType

from .base import BaseDto, TimestampMixin, TrackableMixin


@dataclass(kw_only=True)
class PromocodeDto(BaseDto, TrackableMixin, TimestampMixin):
    code: str
    is_active: bool
    reward_type: PromocodeRewardType
    reward: Optional[int] = None
    plan_snapshot: Optional[dict[str, Any]] = None
    availability: PromocodeAvailability = PromocodeAvailability.ALL
    allowed_telegram_ids: list[int] = field(default_factory=list)
    lifetime: Optional[int] = None
    max_activations: Optional[int] = None


@dataclass(kw_only=True)
class PromocodeActivationDto(BaseDto):
    promocode_id: int
    user_id: int
    activated_at: datetime
