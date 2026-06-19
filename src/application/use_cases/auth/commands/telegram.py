from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError

from src.application.common import Interactor
from src.application.common.dao import UserDao
from src.application.common.policy import Permission
from src.application.common.uow import UnitOfWork
from src.application.dto import UserDto
from src.application.use_cases.auth._telegram import verify_telegram_auth
from src.application.use_cases.user.commands.web_registration import (
    RegisterWebUser,
    RegisterWebUserDto,
)
from src.core.config import AppConfig
from src.core.enums import AuthType


@dataclass
class TelegramAuthData:
    id: int
    first_name: str
    last_name: "str | None"
    username: "str | None"
    payload: dict[str, Any]


class AuthenticateTelegram(Interactor[TelegramAuthData, UserDto]):
    required_permission = None

    def __init__(
        self,
        config: AppConfig,
        user_dao: UserDao,
        register_web_user: RegisterWebUser,
    ) -> None:
        self.config = config
        self.user_dao = user_dao
        self.register_web_user = register_web_user

    async def _execute(self, actor: UserDto, data: TelegramAuthData) -> UserDto:
        bot_token = self.config.bot.token.get_secret_value()
        if not verify_telegram_auth(data.payload, bot_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Telegram auth data",
            )

        user = await self.user_dao.get_by_telegram_id(data.id)
        if user:
            if user.is_blocked:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is blocked")
            return user

        name_parts = [data.first_name]
        if data.last_name:
            name_parts.append(data.last_name)

        new_user = UserDto(
            telegram_id=data.id,
            auth_type=AuthType.TELEGRAM,
            username=data.username,
            name=" ".join(name_parts),
            language=self.config.default_locale,
        )

        try:
            return await self.register_web_user.system(RegisterWebUserDto(user=new_user))
        except IntegrityError as e:
            existing = await self.user_dao.get_by_telegram_id(data.id)
            if existing:
                return existing
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="User creation conflict"
            ) from e


@dataclass
class LinkTelegramData:
    id: int
    username: "str | None"
    payload: dict[str, Any]


class LinkTelegram(Interactor[LinkTelegramData, UserDto]):
    required_permission = Permission.PUBLIC

    def __init__(self, config: AppConfig, uow: UnitOfWork, user_dao: UserDao) -> None:
        self.config = config
        self.uow = uow
        self.user_dao = user_dao

    async def _execute(self, actor: UserDto, data: LinkTelegramData) -> UserDto:
        bot_token = self.config.bot.token.get_secret_value()
        if not verify_telegram_auth(data.payload, bot_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Telegram auth data",
            )

        if actor.telegram_id == data.id:
            return actor

        if actor.telegram_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Already linked to a different Telegram account",
            )

        existing = await self.user_dao.get_by_telegram_id(data.id)
        if existing and existing.id != actor.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Telegram account already linked to another user",
            )

        actor.telegram_id = data.id
        if data.username is not None:
            actor.username = data.username

        async with self.uow:
            updated = await self.user_dao.update(actor)
            if not updated:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found during Telegram link",
                )
            await self.uow.commit()
        return updated
