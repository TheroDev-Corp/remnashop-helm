from dataclasses import dataclass
from typing import Optional

from loguru import logger

from src.application.common import Cryptographer, EventPublisher, Interactor
from src.application.common.dao import SettingsDao, UserDao
from src.application.common.uow import UnitOfWork
from src.application.dto import UserDto
from src.application.events import BlacklistRegistrationAttemptEvent, UserRegisteredEvent
from src.application.use_cases.referral.commands.attachment import AttachReferral, AttachReferralDto


@dataclass
class RegisterWebUserDto:
    user: UserDto
    referral_code: Optional[str] = None


class RegisterWebUser(Interactor[RegisterWebUserDto, UserDto]):
    required_permission = None

    def __init__(
        self,
        uow: UnitOfWork,
        user_dao: UserDao,
        cryptographer: Cryptographer,
        event_publisher: EventPublisher,
        attach_referral: AttachReferral,
        settings_dao: SettingsDao,
    ) -> None:
        self.uow = uow
        self.user_dao = user_dao
        self.cryptographer = cryptographer
        self.event_publisher = event_publisher
        self.attach_referral = attach_referral
        self.settings_dao = settings_dao

    async def _execute(self, actor: UserDto, data: RegisterWebUserDto) -> UserDto:
        new_user = data.user

        if new_user.telegram_id is not None:
            settings = await self.settings_dao.get()
            if new_user.telegram_id in settings.blacklist.blocked_ids:
                new_user.is_blocked = True

        async def persist(referral_code: str) -> UserDto:
            new_user.referral_code = referral_code
            return await self.user_dao.create(new_user)

        async with self.uow:
            created = await self.uow.persist_with_unique_code(
                generate=lambda: self.cryptographer.generate_unique_code(
                    self.user_dao.get_by_referral_code
                ),
                persist=persist,
                column="referral_code",
            )
            await self.uow.commit()

        if created.is_blocked:
            logger.warning(
                f"New web user '{created.remna_name}' created as blocked (found in blacklist)"
            )
            await self.event_publisher.publish(
                BlacklistRegistrationAttemptEvent(
                    user_id=created.id,
                    telegram_id=created.telegram_id,
                    username=created.username,
                    name=created.name,
                    email=created.email,
                )
            )
            return created

        referrer = None
        if data.referral_code:
            referrer = await self.attach_referral.system(
                AttachReferralDto(user_id=created.id, referral_code=data.referral_code)
            )

        await self.event_publisher.publish(
            UserRegisteredEvent(
                user_id=created.id,
                telegram_id=created.telegram_id,
                username=created.username,
                name=created.name,
                email=created.email,
                referrer_user_id=referrer.id if referrer else None,
                referrer_telegram_id=referrer.telegram_id if referrer else None,
                referrer_email=referrer.email if referrer else None,
                referrer_username=referrer.username if referrer else None,
                referrer_name=referrer.name if referrer else None,
            )
        )

        return created
