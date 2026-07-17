from dataclasses import dataclass
from typing import Optional

from loguru import logger

from src.application.common import Cryptographer, Interactor
from src.application.common.dao import AdLinkDao
from src.application.common.policy import Permission
from src.application.common.uow import UnitOfWork
from src.application.dto import AdLinkDto, UserDto


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
        async with self.uow:
            updated = await self.ad_link_dao.update(data.link)
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
