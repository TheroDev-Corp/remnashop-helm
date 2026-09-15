from typing import List, Optional, Protocol, TypeVar, Union

from packaging.version import Version
from remnapy.models import UserResponseDto
from remnapy.models.hwid import HwidDeviceDto

from src.application.dto import (
    PlanSnapshotDto,
    RemnaSubscriptionDto,
    SquadInfoDto,
    SubscriptionDto,
    UserDto,
)

T = TypeVar("T", SubscriptionDto, RemnaSubscriptionDto)


class Remnawave(Protocol):
    async def try_connection(self) -> Version: ...

    async def create_user(
        self,
        user: UserDto,
        plan: Optional[PlanSnapshotDto] = None,
        subscription: Optional[SubscriptionDto] = None,
    ) -> UserResponseDto: ...

    async def update_user(
        self,
        user: UserDto,
        id: int,
        plan: Optional[PlanSnapshotDto] = None,
        subscription: Optional[SubscriptionDto] = None,
        reset_traffic: bool = False,
    ) -> UserResponseDto: ...

    async def enable_user(self, id: int) -> None: ...

    async def disable_user(self, id: int) -> None: ...

    async def delete_user(self, id: int) -> bool: ...

    async def get_user_by_id(self, id: int) -> Optional[UserResponseDto]: ...

    async def get_user_by_username(self, username: str) -> Optional[UserResponseDto]: ...

    async def resolve_user(
        self,
        user: UserDto,
        remna_id: Optional[int] = None,
    ) -> Optional[UserResponseDto]:
        """Find the panel user that really belongs to `user`.

        Tries the stored `remna_id` first and accepts it only if the panel user is owned by
        `user` (see `is_owned_by`), then falls back to an exact telegram_id / username lookup.
        Returns None when nothing owned by `user` exists. Every mutation of a panel user must
        go through an id obtained from here — never through a raw stored id or a list `[0]`.
        """
        ...

    @staticmethod
    def is_owned_by(
        remna_user: UserResponseDto,
        user: UserDto,
        stored_remna_id: Optional[int] = None,
    ) -> bool:
        """Telegram users: exact telegramId, else username `rs_<tg>`. Telegram-less users: never
        a Telegram-bound panel user; otherwise username `rs_web_<id>`, email, or
        `stored_remna_id` (the id stored in that user's current subscription, unique by DB guard).
        """
        ...

    async def get_users_by_telegram_id(self, telegram_id: int) -> List[UserResponseDto]:
        """Panel users whose telegramId is exactly `telegram_id` (never a partial match)."""
        ...

    async def get_users_by_email(self, email: str) -> List[UserResponseDto]: ...

    async def get_all_users(self, limit: int, offset: int) -> List[UserResponseDto]: ...

    async def get_devices(self, id: int) -> List[HwidDeviceDto]: ...

    async def delete_device(self, user_id: int, hwid: str) -> Optional[int]: ...

    async def delete_all_devices(self, user_id: int) -> None: ...

    async def drop_connections(self, user_id: int) -> None: ...

    async def reset_traffic(self, id: int) -> Optional[UserResponseDto]: ...

    async def revoke_subscription(self, id: int) -> None: ...

    async def get_squads_available(self) -> bool: ...

    async def get_internal_squads(self) -> List[SquadInfoDto]: ...

    async def get_external_squads(self) -> List[SquadInfoDto]: ...

    def apply_sync(
        self,
        target: T,
        source: Union[SubscriptionDto, RemnaSubscriptionDto],
    ) -> T: ...
