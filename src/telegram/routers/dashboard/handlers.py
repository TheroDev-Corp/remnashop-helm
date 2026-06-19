from adaptix import Retort
from aiogram.types import CallbackQuery, Message
from aiogram_dialog import DialogManager, ShowMode, StartMode
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.kbd import Button
from dishka import FromDishka
from dishka.integrations.aiogram_dialog import inject
from loguru import logger

from src.application.common import Notifier
from src.application.common.dao import TransactionDao, UserDao
from src.application.common.policy import Permission, PermissionPolicy
from src.application.dto import TelegramUserDto, UserDto
from src.application.use_cases.user.queries.search import SearchUsersDto, SmartSearch
from src.core.constants import USER_KEY
from src.telegram.states import DashboardRemnashop, DashboardUsers

from .users.user.handlers import start_user_transaction_window, start_user_window


@inject
async def on_transactions_list(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
    transaction_dao: FromDishka[TransactionDao],
    notifier: FromDishka[Notifier],
) -> None:
    user: TelegramUserDto = dialog_manager.middleware_data[USER_KEY]
    transactions = await transaction_dao.get_all(limit=1)

    if not transactions:
        await notifier.notify_user(user, i18n_key="ntf-user.transactions-empty")
        return

    await dialog_manager.start(
        state=DashboardRemnashop.TRANSACTIONS,
        mode=StartMode.RESET_STACK,
    )


@inject
async def on_smart_search(
    message: Message,
    widget: MessageInput,
    dialog_manager: DialogManager,
    retort: FromDishka[Retort],
    notifier: FromDishka[Notifier],
    user_dao: FromDishka[UserDao],
    smart_search: FromDishka[SmartSearch],
) -> None:
    dialog_manager.show_mode = ShowMode.EDIT
    user: TelegramUserDto = dialog_manager.middleware_data[USER_KEY]

    if not PermissionPolicy.has_permission(user, Permission.USER_SEARCH):
        return

    search_data = SearchUsersDto(
        query=message.text,
        forward_from_id=message.forward_from.id if message.forward_from else None,
        forward_sender_name=message.forward_sender_name if message.forward_sender_name else None,
        is_forwarded_from_bot=message.forward_from.is_bot if message.forward_from else False,
    )

    result = await smart_search(user, search_data)

    if result.found_transaction:
        transaction = result.transaction
        transaction_user = await user_dao.get_by_id(transaction.user_id)  # type: ignore[union-attr]
        if not transaction_user or not transaction_user.telegram_id:
            await notifier.notify_user(user, i18n_key="ntf-user.not-found")
            return
        logger.info(
            f"{user.log} Smart search: transaction '{transaction.payment_id}' "  # type: ignore[union-attr]
            f"-> user '{transaction_user.remna_name}'"
        )
        await start_user_transaction_window(
            manager=dialog_manager,
            target_user_id=transaction_user.id,
            selected_transaction=transaction.payment_id,  # type: ignore[union-attr]
        )
        return

    if result.transaction_searched:
        await notifier.notify_user(user, i18n_key="ntf-user.transaction-not-found")
        return

    found_users = result.users

    if not found_users:
        await notifier.notify_user(user, i18n_key="ntf-user.not-found")

    elif len(found_users) == 1:
        target_user = found_users[0]
        logger.info(f"{user.log} Smart search -> {target_user.log}")
        await start_user_window(manager=dialog_manager, target_user_id=target_user.id)

    else:
        await dialog_manager.start(
            state=DashboardUsers.SEARCH_RESULTS,
            data={"found_users": retort.dump(found_users, list[UserDto])},
        )
