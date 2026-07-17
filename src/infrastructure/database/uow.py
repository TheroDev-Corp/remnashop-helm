from types import TracebackType
from typing import Awaitable, Callable, Optional, Self, Type, TypeVar

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.uow import UnitOfWork

T = TypeVar("T")


def _is_unique_violation(error: IntegrityError, column: str) -> bool:
    # PostgreSQL names auto unique indexes "<table>_<column>_key" and reports the offending
    # column in the DETAIL line ("Key (<column>)=..."). Matching the DBAPI error keeps the
    # check scoped to ``column`` so collisions on other unique columns are not swallowed.
    detail = str(error.orig) if error.orig is not None else str(error)
    return f"_{column}_key" in detail or f"({column})" in detail


class UnitOfWorkImpl(UnitOfWork):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def __aenter__(self) -> Self:
        logger.debug("SQL transaction started")
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        # Session lifecycle is owned by the DI provider (`provide_session` closes it
        # via `async with pool()`). UoW only manages the transaction boundary.
        if exc_type:
            await self.rollback()

    async def commit(self) -> None:
        await self.session.commit()
        logger.debug("SQL transaction committed")

    async def rollback(self) -> None:
        await self.session.rollback()
        logger.warning("SQL transaction rolled back")

    async def persist_with_unique_code(
        self,
        generate: Callable[[], Awaitable[str]],
        persist: Callable[[str], Awaitable[T]],
        column: str,
        retries: int = 5,
    ) -> T:
        for attempt in range(1, retries + 1):
            code = await generate()
            try:
                async with self.session.begin_nested():
                    return await persist(code)
            except IntegrityError as error:
                if attempt == retries or not _is_unique_violation(error, column):
                    raise
                logger.warning(
                    f"Unique code collision on '{column}' "
                    f"(attempt {attempt}/{retries}), regenerating"
                )
        raise RuntimeError(f"Failed to generate a unique '{column}' after {retries} attempts")
