from typing import Optional

from aiogram import Router
from aiogram.filters import JOIN_TRANSITION, LEAVE_TRANSITION, ChatMemberUpdatedFilter
from aiogram.types import ChatMemberUpdated
from dishka import FromDishka

from src.application.dto import TelegramUserDto
from src.application.use_cases.user.commands.blocking import (
    SetBotBlockedStatus,
    SetBotBlockedStatusDto,
)

# For only ChatType.PRIVATE (app/bot/filters/private.py)

router = Router(name=__name__)


@router.my_chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def on_unblocked(
    member: ChatMemberUpdated,
    user: Optional[TelegramUserDto],
    set_bot_blocked_status: FromDishka[SetBotBlockedStatus],
) -> None:
    if not user:
        return

    await set_bot_blocked_status.system(SetBotBlockedStatusDto(user.id, False))


@router.my_chat_member(ChatMemberUpdatedFilter(LEAVE_TRANSITION))
async def on_blocked(
    member: ChatMemberUpdated,
    user: Optional[TelegramUserDto],
    set_bot_blocked_status: FromDishka[SetBotBlockedStatus],
) -> None:
    if not user:
        return

    await set_bot_blocked_status.system(SetBotBlockedStatusDto(user.id, True))
