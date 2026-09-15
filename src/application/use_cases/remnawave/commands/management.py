from dataclasses import dataclass
from datetime import timedelta

from loguru import logger

from src.application.common import Interactor
from src.application.common.dao import SettingsDao, SubscriptionDao, UserDao
from src.application.common.policy import Permission, PermissionPolicy
from src.application.common.remnawave import Remnawave
from src.application.common.uow import UnitOfWork
from src.application.dto import SubscriptionDto, UserDto
from src.core.exceptions import CooldownError, PermissionDeniedError, RemnaUserBindingError
from src.core.utils.time import datetime_now


async def _require_owned_remna_id(
    remnawave: Remnawave,
    subscription_dao: SubscriptionDao,
    user: UserDto,
    subscription: SubscriptionDto,
) -> int:
    """Return the ID of the panel user owned by `user`, healing the stored ID in the caller's UoW.

    Never falls back to the raw stored ID: raises if no owned panel user exists.
    (Kept local instead of importing the subscription package to avoid an import cycle.)
    """
    remna_user = await remnawave.resolve_user(user, subscription.user_remna_id)
    if remna_user is None:
        raise RemnaUserBindingError(
            f"No Remnawave user owned by {user.log} found "
            f"(stored ID '{subscription.user_remna_id}')"
        )

    if remna_user.id != subscription.user_remna_id:
        logger.warning(
            f"Healing RemnaUser ID of subscription '{subscription.id}' for {user.log}: "
            f"'{subscription.user_remna_id}' -> '{remna_user.id}'"
        )
        subscription.user_remna_id = remna_user.id
        await subscription_dao.update(subscription)

    return remna_user.id


@dataclass(frozen=True)
class DeleteUserDeviceDto:
    user_id: int
    hwid: str


class DeleteUserDevice(Interactor[DeleteUserDeviceDto, bool]):
    required_permission = Permission.PUBLIC

    def __init__(
        self,
        user_dao: UserDao,
        subscription_dao: SubscriptionDao,
        remnawave: Remnawave,
        settings_dao: SettingsDao,
        uow: UnitOfWork,
    ) -> None:
        self.user_dao = user_dao
        self.subscription_dao = subscription_dao
        self.remnawave = remnawave
        self.settings_dao = settings_dao
        self.uow = uow

    async def _execute(self, actor: UserDto, data: DeleteUserDeviceDto) -> bool:
        is_self = data.user_id == actor.id
        if not is_self and not PermissionPolicy.has_permission(actor, Permission.USER_EDITOR):
            logger.warning(
                f"{actor.log} denied deleting device of foreign user '{data.user_id}' "
                f"without USER_EDITOR"
            )
            raise PermissionDeniedError()

        settings = await self.settings_dao.get()
        extra = settings.extra.device_single_reset

        if not extra.enabled:
            raise ValueError("Single device reset is disabled")

        target_user = actor if is_self else await self.user_dao.get_by_id(data.user_id)
        if not target_user:
            raise ValueError(f"User '{data.user_id}' not found")

        current_subscription = await self.subscription_dao.get_current(data.user_id)
        if not current_subscription:
            raise ValueError(f"Subscription for user_id '{data.user_id}' not found")

        if extra.cooldown_hours > 0 and current_subscription.device_single_reset_at:
            available_at = current_subscription.device_single_reset_at + timedelta(
                hours=extra.cooldown_hours
            )
            if datetime_now() < available_at:
                raise CooldownError(available_at)

        async with self.uow:
            remna_id = await _require_owned_remna_id(
                self.remnawave, self.subscription_dao, target_user, current_subscription
            )
            remaining_devices = await self.remnawave.delete_device(remna_id, data.hwid)
            await self.remnawave.drop_connections(remna_id)
            current_subscription.device_single_reset_at = datetime_now()
            await self.subscription_dao.update(current_subscription)
            await self.uow.commit()

        logger.info(f"{actor.log} Deleted device '{data.hwid}' for user_id '{data.user_id}'")
        return bool(remaining_devices)


class DeleteUserAllDevices(Interactor[None, None]):
    required_permission = Permission.PUBLIC

    def __init__(
        self,
        subscription_dao: SubscriptionDao,
        remnawave: Remnawave,
        settings_dao: SettingsDao,
        uow: UnitOfWork,
    ) -> None:
        self.subscription_dao = subscription_dao
        self.remnawave = remnawave
        self.settings_dao = settings_dao
        self.uow = uow

    async def _execute(self, actor: UserDto, data: None) -> None:
        settings = await self.settings_dao.get()
        extra = settings.extra.device_all_reset

        if not extra.enabled:
            raise ValueError("All devices reset is disabled")

        current_subscription = await self.subscription_dao.get_current(actor.id)
        if not current_subscription:
            raise ValueError(
                f"User '{actor.remna_name}' has no active subscription or device limit unlimited"
            )

        if extra.cooldown_hours > 0 and current_subscription.device_all_reset_at:
            available_at = current_subscription.device_all_reset_at + timedelta(
                hours=extra.cooldown_hours
            )
            if datetime_now() < available_at:
                raise CooldownError(available_at)

        async with self.uow:
            remna_id = await _require_owned_remna_id(
                self.remnawave, self.subscription_dao, actor, current_subscription
            )
            await self.remnawave.delete_all_devices(remna_id)
            await self.remnawave.drop_connections(remna_id)
            current_subscription.device_all_reset_at = datetime_now()
            await self.subscription_dao.update(current_subscription)
            await self.uow.commit()

        logger.info(f"{actor.log} Deleted all devices and dropped connections")


class ResetUserTraffic(Interactor[int, None]):
    required_permission = Permission.USER_EDITOR

    def __init__(
        self,
        uow: UnitOfWork,
        user_dao: UserDao,
        subscription_dao: SubscriptionDao,
        remnawave: Remnawave,
    ) -> None:
        self.uow = uow
        self.user_dao = user_dao
        self.subscription_dao = subscription_dao
        self.remnawave = remnawave

    async def _execute(self, actor: UserDto, user_id: int) -> None:
        target_user = await self.user_dao.get_by_id(user_id)
        if not target_user:
            raise ValueError(f"User '{user_id}' not found")

        subscription = await self.subscription_dao.get_current(target_user.id)
        if not subscription:
            raise ValueError(f"Subscription for user '{target_user.remna_name}' not found")

        async with self.uow:
            remna_id = await _require_owned_remna_id(
                self.remnawave, self.subscription_dao, target_user, subscription
            )
            try:
                await self.remnawave.reset_traffic(remna_id)
            except Exception as e:
                logger.error(
                    f"Failed to reset traffic in Remnawave for user '{target_user.remna_name}': {e}"
                )
                raise
            await self.uow.commit()

        logger.info(f"{actor.log} Reset traffic for user '{target_user.id}'")


class ReissueSubscription(Interactor[None, None]):
    required_permission = Permission.PUBLIC

    def __init__(
        self,
        subscription_dao: SubscriptionDao,
        remnawave: Remnawave,
        settings_dao: SettingsDao,
        uow: UnitOfWork,
    ) -> None:
        self.subscription_dao = subscription_dao
        self.remnawave = remnawave
        self.settings_dao = settings_dao
        self.uow = uow

    async def _execute(self, actor: UserDto, data: None) -> None:
        settings = await self.settings_dao.get()
        extra = settings.extra.link_reset

        if not extra.enabled:
            raise ValueError("Subscription link reset is disabled")

        current_subscription = await self.subscription_dao.get_current(actor.id)
        if not current_subscription:
            raise ValueError(f"No active subscription for user '{actor.remna_name}'")

        if extra.cooldown_hours > 0 and current_subscription.link_reset_at:
            available_at = current_subscription.link_reset_at + timedelta(
                hours=extra.cooldown_hours
            )
            if datetime_now() < available_at:
                raise CooldownError(available_at)

        async with self.uow:
            remna_id = await _require_owned_remna_id(
                self.remnawave, self.subscription_dao, actor, current_subscription
            )
            await self.remnawave.revoke_subscription(remna_id)
            current_subscription.link_reset_at = datetime_now()
            await self.subscription_dao.update(current_subscription)
            await self.uow.commit()

        logger.info(f"{actor.log} Reissued subscription")


class ReissueUserSubscription(Interactor[int, None]):
    required_permission = Permission.USER_EDITOR

    def __init__(
        self,
        uow: UnitOfWork,
        user_dao: UserDao,
        subscription_dao: SubscriptionDao,
        remnawave: Remnawave,
    ) -> None:
        self.uow = uow
        self.user_dao = user_dao
        self.subscription_dao = subscription_dao
        self.remnawave = remnawave

    async def _execute(self, actor: UserDto, user_id: int) -> None:
        target_user = await self.user_dao.get_by_id(user_id)
        if not target_user:
            raise ValueError(f"User '{user_id}' not found")

        current_subscription = await self.subscription_dao.get_current(target_user.id)

        if not current_subscription:
            raise ValueError(f"No active subscription for user '{target_user.remna_name}'")

        async with self.uow:
            remna_id = await _require_owned_remna_id(
                self.remnawave, self.subscription_dao, target_user, current_subscription
            )
            await self.remnawave.revoke_subscription(remna_id)
            await self.uow.commit()

        logger.info(f"{actor.log} Reissued subscription for user '{target_user.id}'")
