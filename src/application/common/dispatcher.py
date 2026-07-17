from typing import Optional, Protocol

from src.application.dto import BroadcastDto


class PaymentNotificationDispatcher(Protocol):
    async def notify_payments_restored(self, user_ids: list[int]) -> None: ...


class BroadcastDispatcher(Protocol):
    async def start(self, broadcast: BroadcastDto, plan_id: Optional[int]) -> None: ...
    async def delete(self, broadcast: BroadcastDto) -> None: ...
