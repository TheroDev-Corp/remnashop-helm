from pydantic import BaseModel


class ReferralRewardLevelResponse(BaseModel):
    level: int
    value: int


class ReferralProgramResponse(BaseModel):
    enabled: bool
    referral_code: str
    invited_count: int
    invited_with_payment_count: int
    reward_type: str
    reward_strategy: str
    accrual_strategy: str
    max_level: int
    reward_levels: list[ReferralRewardLevelResponse]
