from typing import Optional, cast

from adaptix import Retort
from adaptix.conversion import ConversionRetort
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import UserOAuthProviderDao
from src.application.dto import UserDto
from src.application.dto.user import UserOAuthProviderDto
from src.core.enums import OAuthProvider
from src.infrastructure.database.models import User, UserOAuthProvider


class UserOAuthProviderDaoImpl(UserOAuthProviderDao):
    def __init__(
        self,
        session: AsyncSession,
        retort: Retort,
        conversion_retort: ConversionRetort,
        redis: Redis,
    ) -> None:
        self.session = session
        self.retort = retort
        self.conversion_retort = conversion_retort
        self.redis = redis

        self._convert_to_dto = self.conversion_retort.get_converter(
            UserOAuthProvider,
            UserOAuthProviderDto,
        )
        self._convert_to_dto_list = self.conversion_retort.get_converter(
            list[UserOAuthProvider],
            list[UserOAuthProviderDto],
        )
        self._convert_user_to_dto = self.conversion_retort.get_converter(User, UserDto)

    async def create(self, dto: UserOAuthProviderDto) -> UserOAuthProviderDto:
        db_record = UserOAuthProvider(
            user_id=dto.user_id,
            provider=dto.provider,
            provider_id=dto.provider_id,
        )
        self.session.add(db_record)
        await self.session.flush()

        logger.debug(f"Created OAuth provider '{dto.provider}' for user_id '{dto.user_id}'")
        return self._convert_to_dto(db_record)

    async def get_by_provider(
        self,
        provider: OAuthProvider,
        provider_id: str,
    ) -> Optional[UserDto]:
        stmt = (
            select(User)
            .join(UserOAuthProvider, UserOAuthProvider.user_id == User.id)
            .where(
                UserOAuthProvider.provider == provider,
                UserOAuthProvider.provider_id == provider_id,
            )
        )
        db_user = await self.session.scalar(stmt)

        if db_user:
            logger.debug(
                f"User found by OAuth provider '{provider}' with provider_id '{provider_id}'"
            )
            return self._convert_user_to_dto(db_user)

        logger.debug(
            f"User not found by OAuth provider '{provider}' with provider_id '{provider_id}'"
        )
        return None

    async def get_user_providers(self, user_id: int) -> list[UserOAuthProviderDto]:
        stmt = select(UserOAuthProvider).where(UserOAuthProvider.user_id == user_id)
        result = await self.session.scalars(stmt)
        db_records = cast(list, result.all())

        logger.debug(f"Retrieved '{len(db_records)}' OAuth providers for user_id '{user_id}'")
        return self._convert_to_dto_list(db_records)

    async def delete(self, user_id: int, provider: OAuthProvider) -> bool:
        stmt = (
            delete(UserOAuthProvider)
            .where(
                UserOAuthProvider.user_id == user_id,
                UserOAuthProvider.provider == provider,
            )
            .returning(UserOAuthProvider.id)
        )
        result = await self.session.execute(stmt)
        deleted_id = result.scalar_one_or_none()

        if deleted_id:
            logger.debug(f"Deleted OAuth provider '{provider}' for user_id '{user_id}'")
            return True

        logger.debug(f"OAuth provider '{provider}' not found for user_id '{user_id}'")
        return False
