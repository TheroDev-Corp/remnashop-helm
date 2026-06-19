import time
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, ClassVar, Final, Optional

from aiogram import BaseMiddleware, Router
from aiogram.types import CallbackQuery, Message, TelegramObject
from aiogram.types import User as AiogramUser
from loguru import logger

from src.core.enums import MiddlewareEventType

DEFAULT_UPDATE_TYPES: Final[list[MiddlewareEventType]] = [
    MiddlewareEventType.MESSAGE,
    MiddlewareEventType.CALLBACK_QUERY,
]


class EventTypedMiddleware(BaseMiddleware, ABC):
    __event_types__: ClassVar[list[MiddlewareEventType]] = DEFAULT_UPDATE_TYPES

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        inner_duration: list[float] = [0.0]  # list — mutable, safe in closure

        async def wrapped_handler(event: TelegramObject, data: dict[str, Any]) -> Any:
            start = time.perf_counter()
            try:
                return await handler(event, data)
            finally:
                inner_duration[0] = time.perf_counter() - start

        start_ts = time.perf_counter()
        result = await self.middleware_logic(wrapped_handler, event, data)
        total_duration = time.perf_counter() - start_ts
        pure_mw_duration = total_duration - inner_duration[0]

        logger.debug(
            f"Middleware '{self.__class__.__name__}' executed in {pure_mw_duration:.4f}s "
            f"(inner chain '{inner_duration[0]:.4f}s')"
        )

        return result

    def setup_inner(self, router: Router) -> None:
        for event_type in self.__event_types__:
            router.observers[event_type].middleware(self)

        logger.debug(
            f"{self.__class__.__name__} set as INNER for: "
            f"{', '.join(t.value for t in self.__event_types__)}"
        )

    def setup_outer(self, router: Router) -> None:
        for event_type in self.__event_types__:
            router.observers[event_type].outer_middleware(self)

        logger.debug(
            f"{self.__class__.__name__} set as OUTER for: "
            f"{', '.join(t.value for t in self.__event_types__)}"
        )

    @abstractmethod
    async def middleware_logic(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any: ...

    def _get_aiogram_user(self, data: dict[str, Any]) -> Optional[AiogramUser]:
        user = data.get("event_from_user")

        if isinstance(user, dict):
            return AiogramUser(**user)

        return user if isinstance(user, AiogramUser) else None

    async def _delete_previous_message(self, event: TelegramObject) -> None:
        if not isinstance(event, CallbackQuery):
            return

        if not isinstance(event.message, Message) or event.message is None:
            return

        try:
            await event.message.delete()
        except Exception as e:
            logger.debug(f"Failed to delete previous message for '{event.from_user.id}': '{e}'")
