from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.enums import Currency, PaymentGatewayType, PurchaseType, TransactionStatus

from .base import BaseSql
from .timestamp import TimestampMixin
from .user import User


class Transaction(BaseSql, TimestampMixin):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[UUID] = mapped_column(index=True, unique=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )

    status: Mapped[TransactionStatus] = mapped_column(index=True)
    is_test: Mapped[bool]

    purchase_type: Mapped[PurchaseType]
    gateway_type: Mapped[PaymentGatewayType]
    gateway_display_name: Mapped[Optional[str]]
    payment_method: Mapped[Optional[str]]

    pricing: Mapped[dict[str, Any]]
    currency: Mapped[Currency]
    plan_snapshot: Mapped[dict[str, Any]]

    # Fulfilment is tracked separately from the payment status: COMPLETED means "paid" (and is
    # what statistics count), fulfilled_at means the subscription was granted. A COMPLETED row
    # with fulfilled_at IS NULL is retried by the reconciliation task.
    fulfilled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    fulfillment_attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    # Lease while an attempt runs, then the backoff deadline after a failure.
    fulfillment_next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    fulfillment_error: Mapped[Optional[str]] = mapped_column(String)

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
