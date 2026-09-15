from datetime import datetime, timedelta
from typing import Iterable, Optional, Protocol, runtime_checkable
from uuid import UUID

from src.application.dto import GatewayStatsDto, PlanIncomeDto, TransactionDto, UserPaymentStatsDto
from src.core.enums import PaymentGatewayType, TransactionStatus


@runtime_checkable
class TransactionDao(Protocol):
    async def create(self, transaction: TransactionDto) -> TransactionDto: ...

    async def update(self, transaction: TransactionDto) -> Optional[TransactionDto]: ...

    async def get_by_payment_id(self, payment_id: UUID) -> Optional[TransactionDto]: ...

    async def get_by_user(self, user_id: int) -> list[TransactionDto]: ...

    async def get_all(self, limit: int = 100, offset: int = 0) -> list[TransactionDto]: ...

    async def get_by_status(self, status: TransactionStatus) -> list[TransactionDto]: ...

    async def update_status(
        self,
        payment_id: UUID,
        status: TransactionStatus,
    ) -> Optional[TransactionDto]: ...

    async def transition_status(
        self,
        payment_id: UUID,
        new_status: TransactionStatus,
        allowed_current: Iterable[TransactionStatus],
    ) -> Optional[TransactionDto]: ...

    async def exists(self, payment_id: UUID) -> bool: ...

    async def claim_fulfillment(
        self,
        payment_id: UUID,
        max_attempts: int,
        lease: timedelta,
    ) -> Optional[int]:
        """Atomically start a fulfilment attempt of a paid (COMPLETED), unfulfilled transaction.

        Returns the attempt number, or None when it is already fulfilled, not paid, out of
        attempts, or another attempt holds the lease / the backoff has not elapsed.
        """
        ...

    async def mark_fulfilled(self, payment_id: UUID) -> bool:
        """Set fulfilled_at once; False if it was already set (must run in the same DB
        transaction as the subscription write)."""
        ...

    async def mark_fulfillment_failed(
        self,
        payment_id: UUID,
        retry_at: datetime,
        error: str,
    ) -> bool:
        """Record a failed attempt and its retry deadline; True on the first recorded failure."""
        ...

    async def get_unfulfilled_payment_ids(
        self,
        max_attempts: int,
        grace: timedelta,
        limit: int = 50,
    ) -> list[UUID]: ...

    async def cancel_old(self, minutes: int = 30) -> int: ...

    async def count(self) -> int: ...

    async def count_paying_users(self) -> int: ...

    async def count_total(self) -> int: ...

    async def count_completed(self) -> int: ...

    async def count_free(self) -> int: ...

    async def get_gateway_stats(self) -> list[GatewayStatsDto]: ...

    async def get_plan_income(self) -> list[PlanIncomeDto]: ...

    async def get_recent_pending(
        self,
        user_id: int,
        plan_id: int,
        duration_days: int,
        gateway_type: PaymentGatewayType,
    ) -> Optional[TransactionDto]: ...

    async def get_user_payment_stats(
        self,
        user_id: int,
    ) -> tuple[Optional[datetime], list[UserPaymentStatsDto]]: ...
