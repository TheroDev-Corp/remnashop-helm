# ruff: noqa: PLC0415
"""A fake Telegram user that talks to the real Dispatcher through Updates."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from aiogram.types import CallbackQuery, Chat, Message, Update, User
from aiogram_dialog.utils import CB_SEP

from .app import SmokeApp
from .fake_telegram import ButtonRef, buttons_of


class ButtonNotFoundError(AssertionError):
    pass


class TgUser:
    def __init__(
        self,
        app: SmokeApp,
        telegram_id: int,
        first_name: str,
        username: Optional[str] = None,
        language_code: str = "ru",
    ) -> None:
        self.app = app
        self.id = telegram_id
        self.user = User(
            id=telegram_id,
            is_bot=False,
            first_name=first_name,
            username=username,
            language_code=language_code,
        )
        self.chat = Chat(id=telegram_id, type="private", first_name=first_name)
        self._message_ids = iter(range(1, 10_000))

    # ------------------------------------------------------------------ inspection
    @property
    def screen(self) -> Optional[Message]:
        return self.app.session.dialog_message(self.id)

    def buttons(self) -> list[ButtonRef]:
        return buttons_of(self.screen)

    def widget_ids(self) -> list[str]:
        return [
            b.widget_data
            for b in self.buttons()
            if b.widget_data and CB_SEP in (b.callback_data or "")
        ]

    def screen_text(self) -> str:
        message = self.screen
        return (message.text or message.caption or "") if message else ""

    def find(self, target: str) -> ButtonRef:
        buttons = self.buttons()
        for b in buttons:
            if b.widget_data == target:
                return b
        for b in buttons:
            if b.widget_data and b.widget_data.startswith(target + ":"):
                return b
        for b in buttons:  # ListGroup inner buttons: "<list_id>:<item_id>:<button_id>"
            data = b.widget_data or ""
            if data.count(":") >= 2 and data.endswith(":" + target):
                return b
        for b in buttons:
            if target in b.text:
                return b
        available = [(b.text, b.widget_data) for b in buttons]
        raise ButtonNotFoundError(f"Button '{target}' not on screen. Available: {available}")

    def has(self, target: str) -> bool:
        try:
            self.find(target)
            return True
        except ButtonNotFoundError:
            return False

    # ------------------------------------------------------------------ actions
    async def send(self, text: str, *, check: bool = True) -> None:
        message = Message(
            message_id=next(self._message_ids),
            date=datetime.now(timezone.utc),
            chat=self.chat,
            from_user=self.user,
            text=text,
        )
        update = Update(update_id=next(self.app.update_ids), message=message)
        await self._feed(update, check, f"send {text!r}")

    async def click(self, target: str, *, check: bool = True) -> None:
        button = self.find(target)
        await self.click_data(button.callback_data or "", check=check, label=f"click {target!r}")

    async def click_data(self, data: str, *, check: bool = True, label: str = "") -> None:
        message = self.screen or self.app.session.last_message(self.id)
        if message is None:
            message = Message(
                message_id=1,
                date=datetime.now(timezone.utc),
                chat=self.chat,
                text="stale",
            )
        callback = CallbackQuery(
            id=str(uuid.uuid4()),
            from_user=self.user,
            chat_instance="smoke",
            message=message,
            data=data,
        )
        await self._feed(
            Update(update_id=next(self.app.update_ids), callback_query=callback),
            check,
            label or f"callback {data!r}",
        )

    async def pre_checkout(self, payload: str, amount: int, *, check: bool = True) -> None:
        from aiogram.types import PreCheckoutQuery

        query = PreCheckoutQuery(
            id=str(uuid.uuid4()),
            from_user=self.user,
            currency="XTR",
            total_amount=amount,
            invoice_payload=payload,
        )
        await self._feed(
            Update(update_id=next(self.app.update_ids), pre_checkout_query=query),
            check,
            "pre_checkout_query",
        )

    async def successful_payment(self, payload: str, amount: int, *, check: bool = True) -> None:
        from aiogram.types import SuccessfulPayment

        message = Message(
            message_id=next(self._message_ids),
            date=datetime.now(timezone.utc),
            chat=self.chat,
            from_user=self.user,
            successful_payment=SuccessfulPayment(
                currency="XTR",
                total_amount=amount,
                invoice_payload=payload,
                telegram_payment_charge_id=f"charge-{uuid.uuid4().hex[:8]}",
                provider_payment_charge_id="",
            ),
        )
        await self._feed(
            Update(update_id=next(self.app.update_ids), message=message),
            check,
            "successful_payment",
        )

    async def _feed(self, update: Update, check: bool, label: str) -> None:
        app = self.app
        try:
            await app.dispatcher.feed_update(app.bot, update)
        except Exception as e:  # escaped every error handler
            import traceback

            app._record("feed_update", e, "".join(traceback.format_exception(e)))
        await app.settle()
        if check:
            app.assert_no_errors(f"{self.user.first_name}: {label}")
