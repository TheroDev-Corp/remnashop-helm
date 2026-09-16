from typing import Any, Awaitable, Callable, Final, Optional, cast

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramEntityTooLarge,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import ErrorEvent as AiogramErrorEvent
from aiogram.types import TelegramObject
from aiogram.types import User as AiogramUser
from aiogram_dialog.api.exceptions import (
    InvalidStackIdError,
    OutdatedIntent,
    UnknownIntent,
    UnknownState,
)
from dishka import AsyncContainer
from loguru import logger

from src.application.common import BotService, EventPublisher, Notifier, Redirect
from src.application.common.dao import UserDao
from src.application.dto import MessagePayloadDto, TempUserDto
from src.application.events import ErrorEvent
from src.application.use_cases.misc.commands.navigation import RedirectMenu
from src.core.config import AppConfig
from src.core.constants import CONFIG_KEY, CONTAINER_KEY
from src.core.enums import Command, MiddlewareEventType
from src.core.exceptions import MenuRenderError, PermissionDeniedError
from src.telegram.keyboards import get_contact_support_keyboard

from .base import EventTypedMiddleware

_IGNORED_BAD_REQUESTS: Final[tuple[str, ...]] = (
    "message is not modified",
    "message to delete not found",
    "message can't be deleted",
    "query is too old and response timeout expired",
    "MESSAGE_ID_INVALID",
    "Bad Request: message to forward not found",
)

# The dialog context/stack is gone (old message, Redis TTL, restart): not a bug.
CONTEXT_LOSS_ERRORS: Final[tuple[type[Exception], ...]] = (
    InvalidStackIdError,
    OutdatedIntent,
    UnknownIntent,
    UnknownState,
)

# Transient Telegram API/transport failures: nothing to fix in the code, don't page admins.
TRANSIENT_TELEGRAM_ERRORS: Final[tuple[type[Exception], ...]] = (
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)


def is_context_loss(exception: BaseException) -> bool:
    return isinstance(exception, CONTEXT_LOSS_ERRORS)


def is_transient_telegram_error(exception: BaseException) -> bool:
    # TelegramEntityTooLarge subclasses TelegramNetworkError but is a real (payload) bug.
    return isinstance(exception, TRANSIENT_TELEGRAM_ERRORS) and not isinstance(
        exception, TelegramEntityTooLarge
    )


async def _answer_callback(event: AiogramErrorEvent) -> None:
    callback = event.update.callback_query
    if callback is None:
        return
    try:
        await callback.answer()
    except Exception as e:
        logger.debug(f"Failed to answer callback query '{callback.id}': '{e}'")


class ErrorMiddleware(EventTypedMiddleware):
    """Outer middleware of the `errors` observer.

    Must be registered AFTER dishka's ContainerMiddleware (see `setup_error_middleware`):
    aiogram's ErrorsMiddleware wraps the update-level dishka container, which is already closed
    when an error event is dispatched. Resolving dependencies from that closed container creates
    sessions nobody closes (SQLAlchemy "non-checked-in connection" GC errors).
    """

    __event_types__ = [MiddlewareEventType.ERROR]

    async def middleware_logic(  # noqa: C901
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        event = cast(AiogramErrorEvent, event)
        exception = event.exception
        aiogram_user: Optional[AiogramUser] = self._get_aiogram_user(data)

        if is_transient_telegram_error(exception):
            user_id = aiogram_user.id if aiogram_user else None
            logger.warning(
                f"Transient Telegram error while processing update '{event.update.update_id}' "
                f"for user '{user_id}': {type(exception).__name__}: {exception}"
            )
            await _answer_callback(event)
            return True

        config: AppConfig = data[CONFIG_KEY]
        container: AsyncContainer = data[CONTAINER_KEY]

        if is_context_loss(exception):
            logger.warning(
                f"Dialog context lost for user '{aiogram_user.id if aiogram_user else None}' "
                f"(update '{event.update.update_id}'): {type(exception).__name__}: {exception}"
            )
            await _answer_callback(event)
            if aiogram_user:
                await self._restart_after_context_loss(event, aiogram_user, container)
            await handler(event, data)
            return True

        bot_service = await container.get(BotService)
        event_publisher = await container.get(EventPublisher)
        notifier = await container.get(Notifier)
        redirect_menu = await container.get(RedirectMenu)

        if isinstance(exception, TelegramBadRequest):
            error_text = str(exception)
            if any(msg in error_text for msg in _IGNORED_BAD_REQUESTS):
                logger.warning(f"Ignored expected TelegramBadRequest: {exception}")
                if aiogram_user:
                    await redirect_menu.system(aiogram_user.id)
                return

        if aiogram_user:
            if isinstance(exception, TelegramForbiddenError):
                # TODO: handle other cases of forbidden error (e.g. blocked by user)
                return

            if isinstance(exception, PermissionDeniedError):
                await notifier.notify_user(
                    TempUserDto.from_aiogram(aiogram_user),
                    i18n_key="ntf-error.permission-denied",
                )
                return

            if not isinstance(exception, MenuRenderError):
                if not self._is_start_command(event):
                    await redirect_menu.system(aiogram_user.id)

                await notifier.notify_user(
                    user=TempUserDto.from_aiogram(aiogram_user),
                    payload=MessagePayloadDto(
                        i18n_key="ntf-error.unknown",
                        reply_markup=get_contact_support_keyboard(bot_service.get_support_url()),
                    ),
                )

        error_event = ErrorEvent(
            **config.build.data,
            #
            telegram_id=aiogram_user.id if aiogram_user else None,
            username=aiogram_user.username if aiogram_user else None,
            name=aiogram_user.full_name if aiogram_user else None,
            #
            exception=exception,
        )

        await event_publisher.publish(error_event)
        logger.exception(exception)

    @staticmethod
    def _is_start_command(event: AiogramErrorEvent) -> bool:
        return (
            event.update.message is not None
            and event.update.message.text == f"/{Command.START.value.command}"
        )

    async def _restart_after_context_loss(
        self,
        event: AiogramErrorEvent,
        aiogram_user: AiogramUser,
        container: AsyncContainer,
    ) -> None:
        try:
            notifier = await container.get(Notifier)
            user_dao = await container.get(UserDao)
            user = await user_dao.get_by_telegram_id(aiogram_user.id)
            if user is None:
                # Not registered yet: /start will create the user and open the menu.
                await notifier.notify_user(
                    TempUserDto.from_aiogram(aiogram_user), i18n_key="ntf-error.lost-context"
                )
                return

            if not self._is_start_command(event):
                redirect = await container.get(Redirect)
                await redirect.to_main_menu(aiogram_user.id)
            await notifier.notify_user(user, i18n_key="ntf-error.lost-context-restart")
        except Exception as e:
            logger.warning(f"Failed to restart dialog for user '{aiogram_user.id}': '{e}'")
