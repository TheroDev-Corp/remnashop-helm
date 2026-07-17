from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from loguru import logger

from src.application.common import Cryptographer, Interactor
from src.application.common.dao import SettingsDao, UserDao
from src.application.common.policy import Permission
from src.application.common.uow import UnitOfWork
from src.application.dto import UserDto
from src.core.exceptions import CooldownError, PermissionDeniedError
from src.core.utils.time import datetime_now


@dataclass(frozen=True)
class SetUserPersonalDiscountDto:
    user_id: int
    discount: int


class SetUserPersonalDiscount(Interactor[SetUserPersonalDiscountDto, None]):
    required_permission = Permission.USER_EDITOR

    def __init__(self, uow: UnitOfWork, user_dao: UserDao) -> None:
        self.uow = uow
        self.user_dao = user_dao

    async def _execute(self, actor: UserDto, data: SetUserPersonalDiscountDto) -> None:
        if not (0 <= data.discount <= 100):
            raise ValueError(f"Invalid discount value '{data.discount}'")

        async with self.uow:
            target_user = await self.user_dao.get_by_id(data.user_id)
            if not target_user:
                raise ValueError(f"User '{data.user_id}' not found")

            if actor.id != target_user.id and not actor.role > target_user.role:
                logger.warning(
                    f"{actor.log} denied editing user '{target_user.id}': "
                    f"target role '{target_user.role}' >= actor role '{actor.role}'"
                )
                raise PermissionDeniedError()

            target_user.personal_discount = data.discount
            await self.user_dao.update(target_user)
            await self.uow.commit()

        logger.info(
            f"{actor.log} Set personal discount to '{data.discount}' for user '{data.user_id}'"
        )


@dataclass(frozen=True)
class SetUserPurchaseDiscountDto:
    user_id: int
    discount: int


class SetUserPurchaseDiscount(Interactor[SetUserPurchaseDiscountDto, None]):
    required_permission = Permission.USER_EDITOR

    def __init__(self, uow: UnitOfWork, user_dao: UserDao) -> None:
        self.uow = uow
        self.user_dao = user_dao

    async def _execute(self, actor: UserDto, data: SetUserPurchaseDiscountDto) -> None:
        if not (0 <= data.discount <= 100):
            raise ValueError(f"Invalid discount value '{data.discount}'")

        async with self.uow:
            target_user = await self.user_dao.get_by_id(data.user_id)
            if not target_user:
                raise ValueError(f"User '{data.user_id}' not found")

            if actor.id != target_user.id and not actor.role > target_user.role:
                logger.warning(
                    f"{actor.log} denied editing user '{target_user.id}': "
                    f"target role '{target_user.role}' >= actor role '{actor.role}'"
                )
                raise PermissionDeniedError()

            target_user.purchase_discount = data.discount
            await self.user_dao.update(target_user)
            await self.uow.commit()

        logger.info(
            f"{actor.log} Set purchase discount to '{data.discount}' for user '{data.user_id}'"
        )


class ToggleUserTrialAvailable(Interactor[int, None]):
    required_permission = Permission.USER_EDITOR

    def __init__(self, uow: UnitOfWork, user_dao: UserDao) -> None:
        self.uow = uow
        self.user_dao = user_dao

    async def _execute(self, actor: UserDto, user_id: int) -> None:
        async with self.uow:
            target_user = await self.user_dao.get_by_id(user_id)
            if not target_user:
                raise ValueError(f"User '{user_id}' not found")

            if actor.id != target_user.id and not actor.role > target_user.role:
                logger.warning(
                    f"{actor.log} denied editing user '{target_user.id}': "
                    f"target role '{target_user.role}' >= actor role '{actor.role}'"
                )
                raise PermissionDeniedError()

            new_value = not target_user.is_trial_available
            await self.user_dao.set_trial_available(target_user.id, new_value)
            await self.uow.commit()

        logger.info(f"{actor.log} Set trial available to '{new_value}' for user '{user_id}'")


@dataclass(frozen=True)
class ChangeUserPointsDto:
    user_id: int
    amount: int


class ChangeUserPoints(Interactor[ChangeUserPointsDto, None]):
    required_permission = Permission.USER_EDITOR

    def __init__(self, uow: UnitOfWork, user_dao: UserDao) -> None:
        self.uow = uow
        self.user_dao = user_dao

    async def _execute(self, actor: UserDto, data: ChangeUserPointsDto) -> None:
        async with self.uow:
            target_user = await self.user_dao.get_by_id(data.user_id)
            if not target_user:
                logger.error(f"{actor.log} User not found with id '{data.user_id}'")
                raise ValueError(f"User '{data.user_id}' not found")

            if actor.id != target_user.id and not actor.role > target_user.role:
                logger.warning(
                    f"{actor.log} denied editing user '{target_user.id}': "
                    f"target role '{target_user.role}' >= actor role '{actor.role}'"
                )
                raise PermissionDeniedError()

            new_points = target_user.points + data.amount
            if new_points < 0:
                raise ValueError(
                    f"{actor.log} Points balance cannot be negative for '{target_user.remna_name}'"
                )

            target_user.points = new_points
            await self.user_dao.update(target_user)
            await self.uow.commit()

        operation = "Added" if data.amount > 0 else "Subtracted"
        logger.info(
            f"{actor.log} {operation} '{abs(data.amount)}' points for '{target_user.remna_name}'"
        )


class ResetUserReferralCode(Interactor[int, None]):
    required_permission = Permission.USER_EDITOR

    def __init__(self, uow: UnitOfWork, user_dao: UserDao, cryptographer: Cryptographer) -> None:
        self.uow = uow
        self.user_dao = user_dao
        self.cryptographer = cryptographer

    async def _execute(self, actor: UserDto, user_id: int) -> None:
        async with self.uow:
            target_user = await self.user_dao.get_by_id(user_id)
            if not target_user:
                raise ValueError(f"User '{user_id}' not found")

            if actor.id != target_user.id and not actor.role > target_user.role:
                logger.warning(
                    f"{actor.log} denied editing user '{target_user.id}': "
                    f"target role '{target_user.role}' >= actor role '{actor.role}'"
                )
                raise PermissionDeniedError()

            async def persist(referral_code: str) -> Optional[UserDto]:
                target_user.referral_code = referral_code
                return await self.user_dao.update(target_user)

            await self.uow.persist_with_unique_code(
                generate=lambda: self.cryptographer.generate_unique_code(
                    self.user_dao.get_by_referral_code
                ),
                persist=persist,
                column="referral_code",
            )
            await self.uow.commit()

        logger.info(f"{actor.log} Reset referral code for user '{user_id}'")


class ResetOwnReferralCode(Interactor[None, None]):
    required_permission = Permission.PUBLIC

    def __init__(
        self,
        uow: UnitOfWork,
        user_dao: UserDao,
        cryptographer: Cryptographer,
        settings_dao: SettingsDao,
    ) -> None:
        self.uow = uow
        self.user_dao = user_dao
        self.cryptographer = cryptographer
        self.settings_dao = settings_dao

    async def _execute(self, actor: UserDto, data: None) -> None:
        settings = await self.settings_dao.get()
        extra = settings.extra.referral_reset

        if not extra.enabled:
            raise ValueError("Referral code reset is disabled")

        if extra.cooldown_hours > 0 and actor.referral_code_reset_at:
            available_at = actor.referral_code_reset_at + timedelta(hours=extra.cooldown_hours)
            if datetime_now() < available_at:
                raise CooldownError(available_at)

        actor.referral_code_reset_at = datetime_now()

        async def persist(referral_code: str) -> Optional[UserDto]:
            actor.referral_code = referral_code
            return await self.user_dao.update(actor)

        async with self.uow:
            await self.uow.persist_with_unique_code(
                generate=lambda: self.cryptographer.generate_unique_code(
                    self.user_dao.get_by_referral_code
                ),
                persist=persist,
                column="referral_code",
            )
            await self.uow.commit()

        logger.info(f"{actor.log} Reset own referral code")
