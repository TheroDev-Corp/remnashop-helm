from aiogram import F, Router
from aiogram.types import CallbackQuery
from dishka import FromDishka
from dishka.integrations.aiogram_dialog import inject

from src.application.common import Notifier
from src.telegram.states import Notification

router = Router(name=__name__)


@inject
@router.callback_query(F.data.startswith(Notification.CLOSE.state))
async def on_close_notification(
    callback: CallbackQuery,
    notifier: FromDishka[Notifier],
) -> None:
    if not callback.message:
        return

    await notifier.delete_notification(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
    )
    await callback.answer()
