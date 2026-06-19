import re

from aiogram.types import InlineKeyboardButton, KeyboardButton
from aiogram_dialog.api.internal import RawKeyboard
from aiogram_dialog.api.protocols import DialogManager
from aiogram_dialog.widgets.kbd.button import (
    Button,
    LoginURLButton,
    SwitchInlineQuery,
    SwitchInlineQueryChosenChatButton,
    SwitchInlineQueryCurrentChat,
    Url,
    WebApp,
)
from aiogram_dialog.widgets.kbd.copy import CopyText
from aiogram_dialog.widgets.kbd.select import Select
from aiogram_dialog.widgets.kbd.state import Back, Cancel, Next, Start, SwitchTo

ButtonVariant = InlineKeyboardButton | KeyboardButton

_TG_EMOJI_RE = re.compile(r'<tg-emoji emoji-id="(\d+)">([^<]*)</tg-emoji>')
_TG_EMOJI_SHORT_RE = re.compile(r'<e id="(\d+)">([^<]*)</e>')


def _extract_tg_emoji(text: str) -> tuple[str, str | None]:
    match = _TG_EMOJI_RE.search(text) or _TG_EMOJI_SHORT_RE.search(text)
    if not match:
        return text, None
    emoji_id = match.group(1)
    clean = _TG_EMOJI_SHORT_RE.sub("", _TG_EMOJI_RE.sub("", text))
    return clean, emoji_id


class _EmojiRenderMixin:
    async def _render_keyboard(
        self,
        data: dict,
        manager: DialogManager,
    ) -> RawKeyboard:
        keyboard: RawKeyboard = await super()._render_keyboard(data, manager)  # type: ignore[misc]

        result: RawKeyboard = []
        for row in keyboard:
            new_row: list[ButtonVariant] = []
            for btn in row:
                if isinstance(btn, InlineKeyboardButton):
                    clean_text, emoji_id = _extract_tg_emoji(btn.text)
                    if emoji_id:
                        btn = btn.model_copy(
                            update={
                                "text": clean_text,
                                # text emoji takes priority over Style emoji
                                "icon_custom_emoji_id": emoji_id
                                if not btn.icon_custom_emoji_id
                                else btn.icon_custom_emoji_id,
                            }
                        )
                new_row.append(btn)
            result.append(new_row)

        return result


class EmojiButton(_EmojiRenderMixin, Button): ...


class EmojiUrl(_EmojiRenderMixin, Url): ...


class EmojiWebApp(_EmojiRenderMixin, WebApp): ...  # type: ignore[misc]


class EmojiSwitchTo(_EmojiRenderMixin, SwitchTo): ...


class EmojiStart(_EmojiRenderMixin, Start): ...


class EmojiBack(_EmojiRenderMixin, Back): ...


class EmojiCancel(_EmojiRenderMixin, Cancel): ...


class EmojiNext(_EmojiRenderMixin, Next): ...


class EmojiSelect(_EmojiRenderMixin, Select): ...


class EmojiCopyText(_EmojiRenderMixin, CopyText): ...


class EmojiLoginURLButton(_EmojiRenderMixin, LoginURLButton): ...


class EmojiSwitchInlineQuery(_EmojiRenderMixin, SwitchInlineQuery): ...  # type: ignore[misc]


class EmojiSwitchInlineQueryChosenChatButton(
    _EmojiRenderMixin, SwitchInlineQueryChosenChatButton
): ...


class EmojiSwitchInlineQueryCurrentChat(_EmojiRenderMixin, SwitchInlineQueryCurrentChat): ...
