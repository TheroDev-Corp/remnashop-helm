import re
from dataclasses import dataclass
from typing import Final, Optional

from loguru import logger

from src.application.common import Cryptographer, Interactor
from src.application.common.dao import AdLinkDao
from src.application.common.policy import Permission
from src.application.common.uow import UnitOfWork
from src.application.dto import AdLinkDto, UserDto
from src.core.enums import Deeplink

AD_LINK_CODE_PATTERN: Final = re.compile(r"^[A-Za-z0-9_-]+$")
TELEGRAM_START_PARAM_MAX_LENGTH: Final[int] = 64


def validate_ad_link_code(code: str) -> None:
    """Telegram accepts only [A-Za-z0-9_-] up to 64 chars as a /start payload (`ad_<code>`)."""
    payload = f"{Deeplink.ADVERTISING.value}_{code}"
    if not AD_LINK_CODE_PATTERN.match(code) or len(payload) > TELEGRAM_START_PARAM_MAX_LENGTH:
        raise ValueError(f"Invalid ad link code '{code}'")


@dataclass(frozen=True)
class CreateAdLinkDto:
    name: str
    code: Optional[str] = None


class CreateAdLink(Interactor[CreateAdLinkDto, AdLinkDto]):
    required_permission = Permission.VIEW_ADVERTISING

    def __init__(
        self,
        uow: UnitOfWork,
        ad_link_dao: AdLinkDao,
        cryptographer: Cryptographer,
    ) -> None:
        self.uow = uow
        self.ad_link_dao = ad_link_dao
        self.cryptographer = cryptographer

    async def _execute(self, actor: UserDto, data: CreateAdLinkDto) -> AdLinkDto:
        async with self.uow:
            if data.code:
                validate_ad_link_code(data.code)
                existing = await self.ad_link_dao.get_by_code(data.code)
                if existing:
                    raise ValueError(f"Ad link with code '{data.code}' already exists")
                created = await self.ad_link_dao.create(
                    AdLinkDto(name=data.name, code=data.code, is_active=True)
                )
            else:

                async def persist(code: str) -> AdLinkDto:
                    return await self.ad_link_dao.create(
                        AdLinkDto(name=data.name, code=code, is_active=True)
                    )

                created = await self.uow.persist_with_unique_code(
                    generate=lambda: self.cryptographer.generate_unique_code(
                        self.ad_link_dao.get_by_code
                    ),
                    persist=persist,
                    column="code",
                )
            await self.uow.commit()

        logger.info(
            f"Ad link '{data.name}' created with code '{created.code}' by {actor.remna_name}"
        )
        return created


@dataclass(frozen=True)
class UpdateAdLinkDto:
    link: AdLinkDto


class UpdateAdLink(Interactor[UpdateAdLinkDto, Optional[AdLinkDto]]):
    required_permission = Permission.VIEW_ADVERTISING

    def __init__(self, uow: UnitOfWork, ad_link_dao: AdLinkDao) -> None:
        self.uow = uow
        self.ad_link_dao = ad_link_dao

    async def _execute(self, actor: UserDto, data: UpdateAdLinkDto) -> Optional[AdLinkDto]:
        link = data.link
        validate_ad_link_code(link.code)

        async with self.uow:
            existing = await self.ad_link_dao.get_by_code(link.code)
            if existing and existing.id != link.id:
                raise ValueError(f"Ad link with code '{link.code}' already exists")
            # The dialog rebuilds the DTO from dialog_data, so its change tracking is empty:
            # write every field explicitly.
            updated = await self.ad_link_dao.update(link.as_fully_changed())
            await self.uow.commit()

        if updated:
            logger.info(f"Ad link id={data.link.id} updated by {actor.log}")

        return updated


class DeleteAdLink(Interactor[int, bool]):
    required_permission = Permission.VIEW_ADVERTISING

    def __init__(self, uow: UnitOfWork, ad_link_dao: AdLinkDao) -> None:
        self.uow = uow
        self.ad_link_dao = ad_link_dao

    async def _execute(self, actor: UserDto, link_id: int) -> bool:
        async with self.uow:
            deleted = await self.ad_link_dao.delete(link_id)
            await self.uow.commit()

        if deleted:
            logger.info(f"Ad link id={link_id} deleted by {actor.log}")

        return deleted
