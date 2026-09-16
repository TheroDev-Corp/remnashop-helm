# ruff: noqa: PLC0415
"""Named admin flows: dashboard sections, user card buttons, shop settings, broadcast preview."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]


async def _owner_dashboard(app):
    from tests.smoke.harness.client import TgUser

    owner = TgUser(app, app.seed.owner_tg, "Owner", "owner")
    await owner.send("/start")
    await owner.click("dashboard")
    assert app.current_state(owner.id) == "Dashboard:MAIN"
    return owner


async def _run_steps(app, user, steps: list[str]) -> None:
    for step in steps:
        if step.startswith("send:"):
            await user.send(step[len("send:") :])
        elif step.startswith("expect:"):
            expected = step[len("expect:") :]
            assert app.current_state(user.id) == expected, (
                f"expected {expected}, got {app.current_state(user.id)}; buttons={user.buttons()}"
            )
        else:
            await user.click(step)


async def _open_user_card(app, owner, telegram_id: int) -> None:
    await owner.click("users")
    assert app.current_state(owner.id) == "DashboardUsers:MAIN"
    await owner.click("search")
    await owner.send(str(telegram_id))
    if app.current_state(owner.id) == "DashboardUsers:SEARCH_RESULTS":
        await owner.click("user")
    assert app.current_state(owner.id) == "DashboardUser:MAIN", owner.buttons()


# --------------------------------------------------------------------------- dashboard
DASHBOARD_SECTIONS = {
    "statistics": "DashboardStatistics:MAIN",
    "users": "DashboardUsers:MAIN",
    "broadcast": "DashboardBroadcast:MAIN",
    "promocodes": "DashboardPromocodes:MAIN",
    "access": "DashboardAccess:MAIN",
    "remnawave": "DashboardRemnawave:MAIN",
    "remnashop": "DashboardRemnashop:MAIN",
    "importer": "DashboardImporter:MAIN",
}


@pytest.mark.parametrize("button,state", DASHBOARD_SECTIONS.items(), ids=list(DASHBOARD_SECTIONS))
async def test_dashboard_section_opens(app, button, state):
    owner = await _owner_dashboard(app)
    await owner.click(button)
    assert app.current_state(owner.id) == state


async def test_admin_role_can_open_dashboard(app):
    from tests.smoke.harness.client import TgUser

    admin = TgUser(app, app.seed.admin_tg, "Admin", "admin")
    await admin.send("/start")
    assert admin.has("dashboard")
    await admin.click("dashboard")
    assert app.current_state(admin.id) == "Dashboard:MAIN"


async def test_regular_user_has_no_dashboard(app):
    from tests.smoke.harness.client import TgUser

    user = TgUser(app, app.seed.subscriber_tg, "Subscriber", "subscriber")
    await user.send("/start")
    assert not user.has("dashboard")


# --------------------------------------------------------------------------- user card
USER_CARD_PATHS: dict[str, list[str]] = {
    "subscription": ["subscription", "expect:DashboardUser:SUBSCRIPTION"],
    "traffic_limit_select": ["subscription", "traffic", "traffic_limit_select"],
    "traffic_limit_input": ["subscription", "traffic", "send:250"],
    "device_limit_select": ["subscription", "device", "device_limit_select"],
    "device_limit_input": ["subscription", "device", "send:5"],
    "reset_traffic": ["subscription", "reset"],
    "devices_list_delete": [
        "subscription",
        "devices",
        "expect:DashboardUser:DEVICES_LIST",
        "delete",
    ],
    "expire_time_select": ["subscription", "expire_time", "duration_select"],
    "expire_time_input": ["subscription", "expire_time", "send:10"],
    "internal_squads": ["subscription", "squads", "internal", "select_squad"],
    "external_squads": ["subscription", "squads", "external", "select_squad"],
    "active_toggle": ["subscription", "active_toggle", "active_toggle"],
    "reissue": ["subscription", "reissue"],
    "delete_subscription": ["subscription", "delete"],
    "referral_reset": ["referral_reset"],
    "statistics": ["statistics", "expect:DashboardUser:STATISTICS"],
    "transactions": ["transactions"],
    "sync_from_remnawave": ["sync", "sync_from_remnawave"],
    "sync_from_remnashop": ["sync", "sync_from_remnashop"],
    "give_subscription": ["give_subscription", "plan_select", "duration_select"],
    "message_preview_send": ["message", "send:Hello from smoke", "preview", "confirm"],
    "give_access": ["give_access"],
    "role_select": ["role", "role_select"],
    "personal_discount": ["discount", "discount_personal", "personal_discount_select"],
    "purchase_discount_input": ["discount", "discount_purchase", "send:15"],
    "trial_toggle": ["trial_toggle"],
    "block_toggle": ["block", "block"],
    "delete_user": ["delete_user", "expect:DashboardUser:CONFIRM_DELETE", "confirm_delete"],
    "back_to_list": ["back"],
}


@pytest.mark.parametrize("steps", USER_CARD_PATHS.values(), ids=list(USER_CARD_PATHS))
async def test_user_card_button(app, steps):
    owner = await _owner_dashboard(app)
    await _open_user_card(app, owner, app.seed.subscriber_tg)
    await _run_steps(app, owner, steps)


async def test_user_card_for_user_without_subscription(app):
    owner = await _owner_dashboard(app)
    await _open_user_card(app, owner, app.seed.newbie_tg)
    assert not owner.has("subscription")
    await _run_steps(app, owner, ["give_subscription", "plan_select", "duration_select"])


# --------------------------------------------------------------------------- remnashop
REMNASHOP_PATHS: dict[str, list[str]] = {
    "admins": ["admins", "expect:DashboardRemnashop:ADMINS"],
    "gateways_select": ["gateways", "select_gateway"],
    "gateways_active_toggle": ["gateways", "active_toggle"],
    "gateways_currency": ["gateways", "default_currency", "currency"],
    "gateways_placement": ["gateways", "placement", "move"],
    "referral_level": ["referral", "enable", "level", "select_level"],
    "referral_reward_type": ["referral", "reward_type", "select_reward"],
    "referral_reward_input": ["referral", "reward", "send:10"],
    "advertising_create": ["advertising", "create", "expect:RemnashopAdvertising:CONFIGURATOR"],
    "plans_open": ["plans", "plan_select", "expect:RemnashopPlans:CONFIGURATOR"],
    "plans_edit_name": ["plans", "plan_select", "name", "send:Smoke Renamed"],
    "plans_durations": ["plans", "plan_select", "durations_prices", "duration_select"],
    "plans_squads": ["plans", "plan_select", "squads", "internal", "squad_select"],
    "plans_create_new": ["plans", "create", "name", "send:Smoke New", "back"],
    "plans_export": ["plans", "export", "plan_select"],
    "notifications_user": ["notifications", "users", "type_select"],
    "notifications_system": ["notifications", "system", "type_select", "route"],
    "logs": ["logs"],
    "backup_open": ["backup", "expect:RemnashopBackup:MAIN"],
    "menu_editor": ["menu_editor", "menu_grid"],
    "extra_device_single": ["extra", "device_single", "device_single_toggle"],
}


@pytest.mark.parametrize("steps", REMNASHOP_PATHS.values(), ids=list(REMNASHOP_PATHS))
async def test_remnashop_section(app, steps):
    owner = await _owner_dashboard(app)
    await owner.click("remnashop")
    await _run_steps(app, owner, steps)


# --------------------------------------------------------------------------- other sections
async def test_promocode_create(app):
    owner = await _owner_dashboard(app)
    await _run_steps(
        app,
        owner,
        [
            "promocodes",
            "create_promo",
            "expect:DashboardPromocodes:CONFIGURATOR",
            "code",
            "send:SMOKE10",
            "expect:DashboardPromocodes:CONFIGURATOR",
            "reward",
            "send:7",
            "expect:DashboardPromocodes:CONFIGURATOR",
            "confirm_create",
        ],
    )


async def test_broadcast_until_preview(app):
    owner = await _owner_dashboard(app)
    await _run_steps(
        app,
        owner,
        [
            "broadcast",
            "ALL",
            "expect:DashboardBroadcast:SEND",
            "content",
            "send:Hello subscribers",
            "back",
            "preview",
        ],
    )
    assert not app.kiq_calls, "broadcast must not be dispatched before confirmation"


@pytest.mark.parametrize(
    "section", ["users", "subscriptions", "transactions", "promocodes", "referrals"]
)
async def test_statistics(app, section):
    owner = await _owner_dashboard(app)
    await _run_steps(app, owner, ["statistics", section])


@pytest.mark.parametrize("section", ["users", "hosts", "nodes", "inbounds"])
async def test_remnawave_section(app, section):
    owner = await _owner_dashboard(app)
    steps = ["remnawave", section, f"expect:DashboardRemnawave:{section.upper()}"]
    await _run_steps(app, owner, steps)


async def test_access_toggles(app):
    owner = await _owner_dashboard(app)
    await _run_steps(app, owner, ["access", "payments", "registration", "conditions", "rules"])


async def test_importer_sync_panel(app):
    owner = await _owner_dashboard(app)
    await _run_steps(app, owner, ["importer", "sync_panel", "sync_panel_start"])
