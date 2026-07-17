from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.enums import ReferralLevel, ReferralRewardType

from .base import BaseSql
from .timestamp import TimestampMixin
from .user import User


class Referral(BaseSql, TimestampMixin):
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    referred_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        unique=True,
    )

    level: Mapped[ReferralLevel]

    referrer: Mapped["User"] = relationship(
        lazy="selectin",
        foreign_keys=[referrer_id],
    )
    referred: Mapped["User"] = relationship(
        lazy="selectin",
        foreign_keys=[referred_id],
    )
    rewards: Mapped[list["ReferralReward"]] = relationship(
        back_populates="referral",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ReferralReward(BaseSql, TimestampMixin):
    __tablename__ = "referral_rewards"

    id: Mapped[int] = mapped_column(primary_key=True)
    referral_id: Mapped[int] = mapped_column(
        ForeignKey("referrals.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )

    type: Mapped[ReferralRewardType]
    amount: Mapped[int]
    is_issued: Mapped[bool]

    referral: Mapped["Referral"] = relationship(
        back_populates="rewards",
        lazy="selectin",
        foreign_keys=[referral_id],
    )

    user: Mapped["User"] = relationship(lazy="selectin", foreign_keys=[user_id])
