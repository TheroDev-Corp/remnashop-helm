from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol, runtime_checkable
from uuid import UUID

from src.application.dto import PlanSubStatsDto, SubscriptionDto, SubscriptionStatsDto
from src.core.enums import SubscriptionStatus


@dataclass(frozen=True)
class RemnaIdConflictDto:
    """Several users' current subscriptions point to the same Remnawave user."""

    user_remna_id: int
    user_ids: list[int]
    telegram_ids: list[Optional[int]]


@runtime_checkable
class SubscriptionDao(Protocol):
    async def create(self, subscription: SubscriptionDto, user_id: int) -> SubscriptionDto:
        """Raises RemnaUserBindingError if another user's current subscription already
        uses `subscription.user_remna_id`."""
        ...

    async def ensure_remna_id_available(self, remna_id: int, user_id: int) -> None:
        """Raises RemnaUserBindingError if another user's current subscription holds `remna_id`.

        Call it before mutating the panel user, so a binding the DB would refuse later never
        leaves the panel and the DB diverged."""
        ...

    async def get_by_id(self, subscription_id: int) -> Optional[SubscriptionDto]: ...

    async def get_by_remna_id(self, remna_id: int) -> Optional[SubscriptionDto]:
        """The *current* subscription bound to `remna_id`; None if absent or ambiguous."""
        ...

    async def get_remna_id_conflicts(self) -> list[RemnaIdConflictDto]: ...

    async def get_all_by_user(self, user_id: int) -> list[SubscriptionDto]: ...

    async def get_current(self, user_id: int) -> Optional[SubscriptionDto]: ...

    async def get_expiring_current(self, until: datetime) -> list[SubscriptionDto]:
        """Active current subscriptions of reachable users that expire between now and `until`."""
        ...

    async def update(self, subscription: SubscriptionDto) -> Optional[SubscriptionDto]:
        """Raises RemnaUserBindingError if `user_remna_id` changed to an id already used by
        another user's current subscription."""
        ...

    async def update_status(
        self,
        subscription_id: int,
        status: SubscriptionStatus,
    ) -> Optional[SubscriptionDto]: ...

    async def exists(self, remna_id: int) -> bool: ...

    async def count_active_by_plan(self, plan_id: int) -> int: ...

    async def get_all_active_internal_squads(self) -> list[UUID]: ...

    async def count_total_trials(self) -> int: ...

    async def count_converted_from_trial(self) -> int: ...

    async def get_stats(self) -> SubscriptionStatsDto: ...

    async def get_plan_sub_stats(self) -> list[PlanSubStatsDto]: ...
