from dataclasses import dataclass
from typing import Optional

from loguru import logger

from src.application.common import Interactor, Notifier
from src.application.common.dao import SettingsDao, UserDao, WaitlistDao
from src.application.dto import SettingsDto, TempUserDto, UserDto
from src.core.enums import AccessMode


@dataclass(frozen=True)
class CheckAccessDto:
    temp_user: TempUserDto
    is_payment_event: bool
    is_referral_event: bool
    is_ad_link_event: bool = False

    @property
    def telegram_id(self) -> int:
        return self.temp_user.telegram_id


class CheckAccess(Interactor[CheckAccessDto, bool]):
    required_permission = None

    def __init__(
        self,
        user_dao: UserDao,
        settings_dao: SettingsDao,
        waitlist_dao: WaitlistDao,
        notifier: Notifier,
    ) -> None:
        self.user_dao = user_dao
        self.settings_dao = settings_dao
        self.waitlist_dao = waitlist_dao
        self.notifier = notifier

    async def _execute(self, actor: UserDto, data: CheckAccessDto) -> bool:
        user: Optional[UserDto] = await self.user_dao.get_by_telegram_id(data.telegram_id)
        settings = await self.settings_dao.get()

        if user:
            if user.is_blocked:
                logger.info(f"Access denied for user '{data.telegram_id}' because they are blocked")
                return False

            if user.is_privileged:
                logger.info(f"Access allowed for privileged user '{data.telegram_id}'")
                return True

        if settings.access.mode == AccessMode.RESTRICTED:
            await self.notifier.notify_user(data.temp_user, i18n_key="ntf-access.maintenance")
            logger.info(f"Access denied for user '{data.telegram_id}' due to restricted mode")
            return False

        if data.is_payment_event and not settings.access.payments_allowed:
            await self.notifier.notify_user(
                user=data.temp_user, i18n_key="ntf-access.payments-disabled"
            )
            logger.info(
                f"Access denied for payment event for user '{data.telegram_id}' "
                f"because payments are disabled"
            )
            if user:
                return await self._manage_waitlist(data.telegram_id)
            return False

        if user:
            return True

        return await self._process_new_user(data, settings)

    async def _process_new_user(self, data: CheckAccessDto, settings: SettingsDto) -> bool:
        if not settings.access.registration_allowed:
            await self.notifier.notify_user(
                user=data.temp_user,
                i18n_key="ntf-access.registration-disabled",
            )
            logger.info(f"Registration is globally disabled for user '{data.telegram_id}'")
            return False

        if settings.access.mode == AccessMode.INVITED:
            if data.is_referral_event or data.is_ad_link_event:
                logger.info(
                    f"Access allowed for referral/ad-link event for user '{data.telegram_id}'"
                )
                return True

            await self.notifier.notify_user(
                user=data.temp_user,
                i18n_key="ntf-access.registration-invite-only",
            )
            logger.info(f"Access denied for user '{data.telegram_id}' because not a referral")
            return False

        logger.info(f"New user '{data.telegram_id}' allowed to register")
        return True

    async def _manage_waitlist(self, telegram_id: int) -> bool:
        if not await self.waitlist_dao.exists(telegram_id):
            await self.waitlist_dao.add(telegram_id)
            logger.info(f"User '{telegram_id}' added to payment waitlist")
        return False
