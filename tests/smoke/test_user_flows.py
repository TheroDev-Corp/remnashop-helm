# ruff: noqa: PLC0415
"""Named end-user flows fed as real Telegram updates."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]


def _user(app, telegram_id: int, name: str):
    from tests.smoke.harness.client import TgUser

    return TgUser(app, telegram_id, name, name.lower())


async def _start(app, telegram_id: int, name: str):
    user = _user(app, telegram_id, name)
    await user.send("/start")
    assert app.current_state(user.id) == "MainMenu:MAIN"
    return user


async def test_start_registers_unknown_user(app):
    user = await _start(app, 555_000_001, "Stranger")
    from sqlalchemy import text

    async with app.container() as c:  # APP scope engine
        from sqlalchemy.ext.asyncio import AsyncEngine

        engine = await c.get(AsyncEngine)
        async with engine.connect() as conn:
            count = await conn.scalar(
                text("SELECT count(*) FROM users WHERE telegram_id = :tg"), {"tg": user.id}
            )
    assert count == 1


@pytest.mark.parametrize("command", ["/help", "/rules", "/paysupport"])
async def test_commands(app, command):
    user = await _start(app, app.seed.subscriber_tg, "Subscriber")
    before = len(app.session.calls)
    await user.send(command)
    assert any(type(c).__name__ == "SendMessage" for c in app.session.calls[before:])


async def test_subscriber_devices(app):
    user = await _start(app, app.seed.subscriber_tg, "Subscriber")
    await user.click("devices")
    assert app.current_state(user.id) == "MainMenu:DEVICES"
    assert ("GET", f"/hwid/devices/{app.seed.subscriber_remna_id}") in [
        (m, p) for m, p, _ in app.panel.requests
    ]
    await user.click("device_item")
    assert app.current_state(user.id) == "MainMenu:DEVICE_CONFIRM_DELETE"
    await user.click("confirm_delete")
    await user.click("delete_all")
    assert app.current_state(user.id) == "MainMenu:DEVICE_CONFIRM_DELETE_ALL"
    await user.click("confirm_delete_all")


async def test_subscriber_reissue_subscription(app):
    user = await _start(app, app.seed.subscriber_tg, "Subscriber")
    await user.click("devices")
    await user.click("reissue")
    assert app.current_state(user.id) == "MainMenu:DEVICE_CONFIRM_REISSUE"
    await user.click("confirm_reissue")
    assert any(p.endswith("/actions/revoke") for _, p, _ in app.panel.requests)


async def test_invite_section(app):
    user = await _start(app, app.seed.subscriber_tg, "Subscriber")
    await user.click("invite")
    assert app.current_state(user.id) == "MainMenu:INVITE"
    await user.click("about")
    assert app.current_state(user.id) == "MainMenu:INVITE_ABOUT"
    await user.click("back")
    await user.click("qr")
    assert "SendPhoto" in app.session.call_names()


async def test_promocode_unknown_code(app):
    user = await _start(app, app.seed.subscriber_tg, "Subscriber")
    await user.click("payment_subscription")
    assert app.current_state(user.id) == "Subscription:MAIN"
    await user.click("goto_promocode")
    assert app.current_state(user.id) == "Subscription:PROMOCODE"
    await user.send("NO-SUCH-CODE")


STEP_ORDER = [
    ("Subscription:MAIN", "payment_NEW"),
    ("Subscription:PLANS", "payment_select_plan"),
    ("Subscription:PLAN", "payment_select_plan"),
    ("Subscription:DURATION", "payment_select_duration"),
    ("Subscription:PAYMENT_METHOD", "payment_select_payment_method"),
]


async def _walk_purchase(app, user) -> None:
    await user.click("payment_subscription")
    for _ in range(8):
        state = app.current_state(user.id)
        if state == "Subscription:CONFIRM":
            return
        target = next((w for s, w in STEP_ORDER if s == state), None)
        assert target is not None, f"unexpected purchase step {state}: {user.buttons()}"
        await user.click(target)
    raise AssertionError(f"purchase did not reach CONFIRM, stuck at {app.current_state(user.id)}")


async def test_newbie_purchase_flow_until_payment_link(app):
    user = await _start(app, app.seed.newbie_tg, "Newbie")
    await _walk_purchase(app, user)
    assert "CreateInvoiceLink" in app.session.call_names()
    assert any(b.url and "$smoke_invoice" in b.url for b in user.buttons()), user.buttons()


async def test_subscriber_renew_flow_until_payment_link(app):
    user = await _start(app, app.seed.subscriber_tg, "Subscriber")
    await user.click("payment_subscription")
    renew = "payment_RENEW" if user.has("payment_RENEW") else "payment_NEW"
    await user.click(renew)
    for _ in range(8):
        state = app.current_state(user.id)
        if state == "Subscription:CONFIRM":
            break
        target = next((w for s, w in STEP_ORDER if s == state), None)
        assert target is not None, f"unexpected renew step {state}: {user.buttons()}"
        await user.click(target)
    assert app.current_state(user.id) == "Subscription:CONFIRM"
    assert "CreateInvoiceLink" in app.session.call_names()


def _last_invoice(app):
    invoices = [c for c in app.session.calls if type(c).__name__ == "CreateInvoiceLink"]
    assert invoices, "no Telegram Stars invoice link was created"
    return invoices[-1]


async def _scalar(app, sql: str, **params):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncEngine

    engine = await app.container.get(AsyncEngine)
    async with engine.connect() as conn:
        return await conn.scalar(text(sql), params)


async def test_newbie_pays_with_telegram_stars(app):
    user = await _start(app, app.seed.newbie_tg, "Newbie")
    await _walk_purchase(app, user)
    invoice = _last_invoice(app)
    amount = invoice.prices[0].amount

    await user.pre_checkout(invoice.payload, amount)
    answers = [c for c in app.session.calls if type(c).__name__ == "AnswerPreCheckoutQuery"]
    assert answers and answers[-1].ok is True, answers

    await user.successful_payment(invoice.payload, amount)
    status = await _scalar(
        app, "SELECT status::text FROM transactions WHERE payment_id = :pid", pid=invoice.payload
    )
    assert status == "COMPLETED"
    assert app.current_state(user.id) == "Subscription:SUCCESS"
    has_subscription = await _scalar(
        app,
        "SELECT current_subscription_id IS NOT NULL FROM users WHERE telegram_id = :tg",
        tg=user.id,
    )
    assert has_subscription
    assert any(u.get("telegramId") == user.id for u in app.panel.users.values())


async def test_pre_checkout_with_invalid_payload_is_rejected(app):
    user = await _start(app, app.seed.newbie_tg, "Newbie")
    await user.pre_checkout("not-a-uuid", 100)
    answers = [c for c in app.session.calls if type(c).__name__ == "AnswerPreCheckoutQuery"]
    assert answers and answers[-1].ok is False


async def test_owner_test_payment_is_refunded(app):
    user = await _start(app, app.seed.owner_tg, "Owner")
    await _walk_purchase(app, user)
    invoice = _last_invoice(app)
    amount = invoice.prices[0].amount
    await user.pre_checkout(invoice.payload, amount)
    await user.successful_payment(invoice.payload, amount)
    assert "RefundStarPayment" in app.session.call_names()
    status = await _scalar(
        app, "SELECT status::text FROM transactions WHERE payment_id = :pid", pid=invoice.payload
    )
    assert status == "CANCELED"


async def test_newbie_free_trial_activation(app):
    user = await _start(app, app.seed.newbie_tg, "Newbie")
    assert user.has("trial_free"), user.buttons()
    await user.click("trial_free")
    assert app.current_state(user.id) == "Subscription:TRIAL"
    created = [u for u in app.panel.users.values() if u.get("telegramId") == user.id]
    assert created, app.panel.requests


async def test_trial_button_on_old_menu_after_trial_used(app):
    user = await _start(app, app.seed.newbie_tg, "Newbie")
    stale_trial = user.find("trial_free").callback_data or ""
    await user.click("trial_free")
    assert app.current_state(user.id) == "Subscription:TRIAL"
    await user.send("/start")
    from aiogram_dialog.api.exceptions import UnknownIntent

    # /start reset the dialog stack, so the old menu's context is gone: handled as a lost
    # context (warning + back to main menu), never as a crash of on_get_trial.
    with app.expect_errors(UnknownIntent):
        await user.click_data(stale_trial)
    assert app.current_state(user.id) == "MainMenu:MAIN"


async def test_stale_callback_with_unknown_intent(app):
    from aiogram_dialog.api.exceptions import UnknownIntent
    from aiogram_dialog.utils import CB_SEP

    user = await _start(app, app.seed.subscriber_tg, "Subscriber")
    with app.expect_errors(UnknownIntent) as expected:
        await user.click_data(f"deadbeefdeadbeef{CB_SEP}devices")
    assert expected, "stale callback did not raise UnknownIntent"
    # Recovered: the user is put back on the main menu.
    assert app.current_state(user.id) == "MainMenu:MAIN"


async def test_stale_callback_from_old_menu_message(app):
    from aiogram_dialog.api.exceptions import OutdatedIntent, UnknownIntent

    user = await _start(app, app.seed.subscriber_tg, "Subscriber")
    old_invite = user.find("invite").callback_data
    await user.send("/start")  # resets the stack; the old intent is gone
    with app.expect_errors(UnknownIntent, OutdatedIntent):
        await user.click_data(old_invite or "")
    assert app.current_state(user.id) == "MainMenu:MAIN"
