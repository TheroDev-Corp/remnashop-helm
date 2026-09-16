# ruff: noqa: PLC0415
"""In-process stand-in for the Telegram Bot API.

`FakeTelegramSession` is an aiogram session: every Bot API call made by the bot (aiogram-dialog
renders, notifier messages, answerCallbackQuery, webhook/commands setup, invoice links, ...)
is recorded and answered with a plausible object, so the real `Bot` class and all of its
method models/validation are exercised. Live messages are tracked per chat, which lets tests
read the current dialog screen and "click" its inline buttons.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    CopyMessage,
    CreateInvoiceLink,
    DeleteMessage,
    DeleteMessages,
    EditMessageCaption,
    EditMessageMedia,
    EditMessageReplyMarkup,
    EditMessageText,
    GetChatMember,
    GetFile,
    GetMe,
    GetMyName,
    GetWebhookInfo,
    SendAnimation,
    SendDocument,
    SendMessage,
    SendPhoto,
    SendVideo,
    TelegramMethod,
)
from aiogram.types import (
    Animation,
    BotName,
    Chat,
    ChatMemberMember,
    Document,
    File,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageId,
    PhotoSize,
    User,
    Video,
)
from aiogram_dialog.utils import CB_SEP

BOT_ID = 123456
BOT_USERNAME = "smoke_test_bot"


class UnexpectedTelegramCallError(AssertionError):
    pass


@dataclass
class ButtonRef:
    text: str
    callback_data: Optional[str]
    url: Optional[str]

    @property
    def widget_data(self) -> Optional[str]:
        """Callback data without the aiogram-dialog intent prefix (e.g. `sync` or `select:3`)."""
        if self.callback_data is None:
            return None
        if CB_SEP in self.callback_data:
            return self.callback_data.split(CB_SEP, 1)[1]
        return self.callback_data


class FakeTelegramSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self.chats: dict[int, dict[int, Message]] = defaultdict(dict)
        self._ids = itertools.count(10_000)
        self.bot_user = User(
            id=BOT_ID,
            is_bot=True,
            first_name="Smoke",
            username=BOT_USERNAME,
            can_join_groups=False,
            can_read_all_group_messages=False,
            supports_inline_queries=True,
        )

    # ----------------------------------------------------------------- session protocol
    async def close(self) -> None:
        return None

    async def stream_content(
        self,
        url: str,
        headers: Optional[dict[str, Any]] = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        yield b""

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: Optional[int] = None,
    ) -> Any:
        self.calls.append(method)
        handler = getattr(self, f"_on_{type(method).__name__}", None)
        if handler is not None:
            result = handler(method)
        elif method.__returning__ is bool:
            result = True
        else:
            raise UnexpectedTelegramCallError(
                f"Fake Telegram session has no answer for '{type(method).__name__}'"
            )
        if hasattr(result, "as_"):
            result = result.as_(bot)
        return result

    # ----------------------------------------------------------------- helpers
    def reset(self) -> None:
        self.calls.clear()
        self.chats.clear()

    def call_names(self) -> list[str]:
        return [type(c).__name__ for c in self.calls]

    def _message(
        self,
        chat_id: Any,
        *,
        message_id: Optional[int] = None,
        text: Optional[str] = None,
        caption: Optional[str] = None,
        reply_markup: Any = None,
        media: Optional[str] = None,
    ) -> Message:
        chat_id = int(chat_id)
        message_id = message_id or next(self._ids)
        uid = f"f{message_id}"
        extra: dict[str, Any] = {}
        if media == "photo":
            extra["photo"] = [PhotoSize(file_id=uid, file_unique_id=uid, width=10, height=10)]
        elif media == "video":
            extra["video"] = Video(file_id=uid, file_unique_id=uid, width=10, height=10, duration=1)
        elif media == "animation":
            extra["animation"] = Animation(
                file_id=uid, file_unique_id=uid, width=10, height=10, duration=1
            )
        elif media == "document":
            extra["document"] = Document(file_id=uid, file_unique_id=uid)

        message = Message(
            message_id=message_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id, type="private", first_name="chat"),
            from_user=self.bot_user,
            text=text,
            caption=caption,
            reply_markup=reply_markup if isinstance(reply_markup, InlineKeyboardMarkup) else None,
            **extra,
        )
        self.chats[chat_id][message_id] = message
        return message

    def messages(self, chat_id: int) -> list[Message]:
        return [self.chats[chat_id][k] for k in sorted(self.chats[chat_id])]

    def last_message(self, chat_id: int) -> Optional[Message]:
        msgs = self.messages(chat_id)
        return msgs[-1] if msgs else None

    def dialog_message(self, chat_id: int) -> Optional[Message]:
        """Newest live message carrying an aiogram-dialog keyboard (the current screen)."""
        for message in reversed(self.messages(chat_id)):
            if any(CB_SEP in (b.callback_data or "") for b in buttons_of(message)):
                return message
        return None

    # ----------------------------------------------------------------- method answers
    def _on_GetMe(self, m: GetMe) -> User:  # noqa: N802
        return self.bot_user

    def _on_GetMyName(self, m: GetMyName) -> BotName:  # noqa: N802
        return BotName(name="Smoke Bot")

    def _on_GetWebhookInfo(self, m: GetWebhookInfo) -> Any:  # noqa: N802
        from aiogram.types import WebhookInfo

        return WebhookInfo(url="", has_custom_certificate=False, pending_update_count=0)

    def _on_GetChatMember(self, m: GetChatMember) -> ChatMemberMember:  # noqa: N802
        return ChatMemberMember(user=User(id=m.user_id, is_bot=False, first_name="member"))

    def _on_GetFile(self, m: GetFile) -> File:  # noqa: N802
        return File(file_id=m.file_id, file_unique_id=m.file_id, file_path=f"files/{m.file_id}")

    def _on_CreateInvoiceLink(self, m: CreateInvoiceLink) -> str:  # noqa: N802
        return f"https://t.me/$smoke_invoice_{next(self._ids)}"

    def _on_AnswerCallbackQuery(self, m: AnswerCallbackQuery) -> bool:  # noqa: N802
        return True

    def _on_SendMessage(self, m: SendMessage) -> Message:  # noqa: N802
        return self._message(m.chat_id, text=m.text, reply_markup=m.reply_markup)

    def _on_SendPhoto(self, m: SendPhoto) -> Message:  # noqa: N802
        return self._message(
            m.chat_id, caption=m.caption, reply_markup=m.reply_markup, media="photo"
        )

    def _on_SendVideo(self, m: SendVideo) -> Message:  # noqa: N802
        return self._message(
            m.chat_id, caption=m.caption, reply_markup=m.reply_markup, media="video"
        )

    def _on_SendAnimation(self, m: SendAnimation) -> Message:  # noqa: N802
        return self._message(
            m.chat_id, caption=m.caption, reply_markup=m.reply_markup, media="animation"
        )

    def _on_SendDocument(self, m: SendDocument) -> Message:  # noqa: N802
        return self._message(
            m.chat_id, caption=m.caption, reply_markup=m.reply_markup, media="document"
        )

    def _on_CopyMessage(self, m: CopyMessage) -> MessageId:  # noqa: N802
        return MessageId(message_id=self._message(m.chat_id, text="copy").message_id)

    def _existing(self, chat_id: Any, message_id: Optional[int]) -> Message:
        from aiogram.exceptions import TelegramBadRequest

        chat = self.chats.get(int(chat_id), {})
        if message_id not in chat:
            raise TelegramBadRequest(
                method=None,  # type: ignore[arg-type]
                message="Bad Request: message to edit not found",
            )
        return chat[message_id]

    def _on_EditMessageText(self, m: EditMessageText) -> Message:  # noqa: N802
        self._existing(m.chat_id, m.message_id)
        return self._message(
            m.chat_id, message_id=m.message_id, text=m.text, reply_markup=m.reply_markup
        )

    def _on_EditMessageCaption(self, m: EditMessageCaption) -> Message:  # noqa: N802
        old = self._existing(m.chat_id, m.message_id)
        return self._message(
            m.chat_id,
            message_id=m.message_id,
            caption=m.caption,
            reply_markup=m.reply_markup,
            media=_media_kind(old),
        )

    def _on_EditMessageReplyMarkup(self, m: EditMessageReplyMarkup) -> Message:  # noqa: N802
        old = self._existing(m.chat_id, m.message_id)
        return self._message(
            m.chat_id,
            message_id=m.message_id,
            text=old.text,
            caption=old.caption,
            reply_markup=m.reply_markup,
            media=_media_kind(old),
        )

    def _on_EditMessageMedia(self, m: EditMessageMedia) -> Message:  # noqa: N802
        self._existing(m.chat_id, m.message_id)
        kind = getattr(m.media, "type", "photo")
        return self._message(
            m.chat_id,
            message_id=m.message_id,
            caption=getattr(m.media, "caption", None),
            reply_markup=m.reply_markup,
            media=str(getattr(kind, "value", kind)),
        )

    def _on_DeleteMessage(self, m: DeleteMessage) -> bool:  # noqa: N802
        self.chats.get(int(m.chat_id), {}).pop(m.message_id, None)
        return True

    def _on_DeleteMessages(self, m: DeleteMessages) -> bool:  # noqa: N802
        for message_id in m.message_ids:
            self.chats.get(int(m.chat_id), {}).pop(message_id, None)
        return True


def _media_kind(message: Message) -> Optional[str]:
    for kind in ("photo", "video", "animation", "document"):
        if getattr(message, kind):
            return kind
    return None


def buttons_of(message: Optional[Message]) -> list[ButtonRef]:
    if message is None or not isinstance(message.reply_markup, InlineKeyboardMarkup):
        return []
    result = []
    for row in message.reply_markup.inline_keyboard:
        for button in row:
            if not isinstance(button, InlineKeyboardButton):
                button = InlineKeyboardButton(**button)
            result.append(ButtonRef(button.text, button.callback_data, button.url))
    return result
