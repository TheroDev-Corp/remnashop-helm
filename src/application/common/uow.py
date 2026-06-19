from types import TracebackType
from typing import Awaitable, Callable, Optional, Protocol, Self, Type, TypeVar

T = TypeVar("T")


class UnitOfWork(Protocol):
    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    async def persist_with_unique_code(
        self,
        generate: Callable[[], Awaitable[str]],
        persist: Callable[[str], Awaitable[T]],
        column: str,
        retries: int = 5,
    ) -> T: ...
