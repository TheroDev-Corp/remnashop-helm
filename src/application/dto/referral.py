from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.core.enums import ReferralLevel, ReferralRewardType

from .base import BaseDto, TimestampMixin, TrackableMixin
from .user import UserDto


@dataclass(kw_only=True)
class ReferralDto(BaseDto, TrackableMixin, TimestampMixin):
    level: ReferralLevel

    referrer: "UserDto"
    referred: "UserDto"


@dataclass(kw_only=True)
class ReferralRewardDto(BaseDto, TrackableMixin, TimestampMixin):
    user_id: int

    type: ReferralRewardType
    amount: int
    is_issued: bool = False

    @property
    def rewarded_at(self) -> Optional[datetime]:
        return self.created_at


@dataclass(frozen=True)
class UserReferralStatsDto:
    referrer_telegram_id: Optional[int]
    referrer_email: Optional[str]
    referrer_username: Optional[str]
    referrals_level_1: int
    referrals_level_2: int
    reward_points: int
    reward_days: int
