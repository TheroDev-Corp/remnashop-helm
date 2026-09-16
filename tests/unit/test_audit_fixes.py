"""Regression tests for dashboard, promocode, referral, importer and web-auth fixes."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from adaptix import Retort
from fastapi import HTTPException

from src.application.dto import AdLinkDto, UserDto
from src.application.dto.payment_gateway import MulenPayGatewaySettingsDto
from src.application.dto.referral import ReferralRewardDto
from src.application.events import ReferralRewardFailedEvent
from src.application.use_cases.ad_link.commands.manage import (
    CreateAdLink,
    CreateAdLinkDto,
    UpdateAdLink,
    UpdateAdLinkDto,
)
from src.application.use_cases.auth.commands.email import ChangeEmail, ChangeEmailDto
from src.application.use_cases.gateways.commands.configuration import (
    UpdatePaymentGatewaySettings,
    UpdatePaymentGatewaySettingsDto,
)
from src.application.use_cases.importer.dto import ExportedUserDto
from src.application.use_cases.importer.queries.xui import ExportUsersFromXui
from src.application.use_cases.promocode.commands.activate import (
    ActivatePromocode,
    ActivatePromocodeDto,
)
from src.application.use_cases.referral.commands.rewards import (
    GiveReferrerReward,
    GiveReferrerRewardDto,
)
from src.application.use_cases.remnawave.commands.management import (
    DeleteUserDevice,
    DeleteUserDeviceDto,
    ReissueUserSubscription,
    ResetUserTraffic,
)
from src.application.use_cases.subscription.commands.management import (
    DeleteSubscription,
    ToggleSubscriptionStatus,
)
from src.application.use_cases.subscription.commands.purchase import (
    ActivateTrialSubscription,
    ActivateTrialSubscriptionDto,
)
from src.core.enums import (
    PromocodeRewardType,
    ReferralRewardType,
    Role,
    SubscriptionStatus,
)
from src.core.exceptions import PermissionDeniedError, TrialNotAvailableError
from src.core.utils.time import datetime_now
from src.infrastructure.services.webhook import WebhookService
from src.infrastructure.taskiq.tasks.importer import build_create_user_request
from src.telegram.utils import is_double_click

OWNED_ID = 777


@pytest.fixture
def uow():
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.commit = AsyncMock()
    return uow


def _remna_user(id: int = OWNED_ID) -> MagicMock:
    remna_user = MagicMock()
    remna_user.id = id
    remna_user.status = "ACTIVE"
    remna_user.expire_at = datetime(2027, 1, 1, tzinfo=timezone.utc)
    remna_user.subscription_url = f"https://sub.example.com/{id}"
    return remna_user


def _user(id: int, role: Role, telegram_id: int) -> UserDto:
    return UserDto(id=id, telegram_id=telegram_id, name=f"User {id}", role=role)


# --------------------------------------------------------------------------- device delete


@pytest.fixture
def device_deps(sample_user_dto, sample_subscription_dto):
    user_dao = MagicMock()
    user_dao.get_by_id = AsyncMock(return_value=sample_user_dto)
    sub_dao = MagicMock()
    sub_dao.get_current = AsyncMock(return_value=sample_subscription_dto)
    sub_dao.update = AsyncMock()
    remnawave = MagicMock()
    remnawave.resolve_user = AsyncMock(
        return_value=_remna_user(sample_subscription_dto.user_remna_id)
    )
    remnawave.delete_device = AsyncMock(return_value=1)
    remnawave.drop_connections = AsyncMock()
    remnawave.reset_traffic = AsyncMock()
    remnawave.revoke_subscription = AsyncMock()
    settings = MagicMock()
    settings.extra.device_single_reset.enabled = False
    settings.extra.device_single_reset.cooldown_hours = 24
    settings_dao = MagicMock()
    settings_dao.get = AsyncMock(return_value=settings)
    return user_dao, sub_dao, remnawave, settings_dao


@pytest.mark.asyncio
async def test_admin_device_delete_ignores_self_service_limits(
    device_deps, uow, sample_subscription_dto
):
    user_dao, sub_dao, remnawave, settings_dao = device_deps
    sample_subscription_dto.device_single_reset_at = datetime_now()  # within the cooldown
    sample_subscription_dto._changed_data.clear()
    admin = _user(99, Role.OWNER, 555)
    use_case = DeleteUserDevice(user_dao, sub_dao, remnawave, settings_dao, uow)

    assert await use_case(admin, DeleteUserDeviceDto(user_id=1, hwid="hwid")) is True

    remnawave.delete_device.assert_awaited_once()
    settings_dao.get.assert_not_awaited()
    sub_dao.update.assert_not_awaited()  # the user's own cooldown is not spent


@pytest.mark.asyncio
async def test_self_device_delete_still_respects_setting(device_deps, uow, sample_user_dto):
    user_dao, sub_dao, remnawave, settings_dao = device_deps
    use_case = DeleteUserDevice(user_dao, sub_dao, remnawave, settings_dao, uow)

    with pytest.raises(ValueError):
        await use_case(sample_user_dto, DeleteUserDeviceDto(user_id=sample_user_dto.id, hwid="h"))

    remnawave.delete_device.assert_not_awaited()


# --------------------------------------------------------------------------- role hierarchy


def _role_cases(uow, user_dao, sub_dao, remnawave, settings_dao):
    return {
        "toggle": lambda actor: ToggleSubscriptionStatus(
            uow, user_dao, sub_dao, remnawave
        )._execute(actor, 1),
        "delete": lambda actor: DeleteSubscription(uow, user_dao, sub_dao, remnawave)._execute(
            actor, 1
        ),
        "reset": lambda actor: ResetUserTraffic(uow, user_dao, sub_dao, remnawave)._execute(
            actor, 1
        ),
        "reissue": lambda actor: ReissueUserSubscription(
            uow, user_dao, sub_dao, remnawave
        )._execute(actor, 1),
        "device": lambda actor: DeleteUserDevice(
            user_dao, sub_dao, remnawave, settings_dao, uow
        )._execute(actor, DeleteUserDeviceDto(user_id=1, hwid="h")),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["toggle", "delete", "reset", "reissue", "device"])
async def test_admin_cannot_touch_higher_role_subscription(device_deps, uow, sample_user_dto, case):
    user_dao, sub_dao, remnawave, settings_dao = device_deps
    sample_user_dto.role = Role.OWNER
    dev = _user(99, Role.DEV, 555)

    with pytest.raises(PermissionDeniedError):
        await _role_cases(uow, user_dao, sub_dao, remnawave, settings_dao)[case](dev)

    remnawave.resolve_user.assert_not_awaited()


# --------------------------------------------------------------------------- trial guard


@pytest.mark.asyncio
async def test_trial_never_replaces_existing_subscription(
    uow, sample_user_dto, sample_plan_dto, sample_subscription_dto
):
    sample_user_dto.is_trial_available = True
    sub_dao = MagicMock()
    sub_dao.get_current = AsyncMock(return_value=sample_subscription_dto)
    remnawave = MagicMock()
    remnawave.create_user = AsyncMock()
    remnawave.resolve_user = AsyncMock()
    use_case = ActivateTrialSubscription(uow, MagicMock(), sub_dao, remnawave, MagicMock())

    with pytest.raises(TrialNotAvailableError):
        await use_case.system(ActivateTrialSubscriptionDto(sample_user_dto, sample_plan_dto))

    remnawave.resolve_user.assert_not_awaited()
    remnawave.create_user.assert_not_awaited()


# --------------------------------------------------------------------------- promocodes


def _promocode(sub_dao, user_dao, remnawave, plan):
    promo = MagicMock(
        reward_type=PromocodeRewardType.SUBSCRIPTION,
        reward=None,
        id=1,
        code="X",
        plan_snapshot={"name": "plan"},
    )
    promocode_dao = MagicMock()
    promocode_dao.create_activation = AsyncMock()
    publisher = MagicMock()
    publisher.publish = AsyncMock()
    retort = MagicMock()
    retort.load = MagicMock(return_value=plan)
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.commit = AsyncMock()
    return ActivatePromocode(
        uow=uow,
        promocode_dao=promocode_dao,
        user_dao=user_dao,
        subscription_dao=sub_dao,
        remnawave=remnawave,
        validate_promocode=AsyncMock(return_value=promo),
        event_publisher=publisher,
        retort=retort,
    )


@pytest.fixture
def promo_deps(sample_subscription_dto):
    sub_dao = MagicMock()
    sub_dao.get_current = AsyncMock(return_value=sample_subscription_dto)
    sub_dao.update = AsyncMock()
    sub_dao.create = AsyncMock()
    sub_dao.ensure_remna_id_available = AsyncMock()
    user_dao = MagicMock()
    user_dao.update = AsyncMock()
    remnawave = MagicMock()
    remnawave.resolve_user = AsyncMock(return_value=_remna_user())
    remnawave.update_user = AsyncMock(return_value=_remna_user())
    remnawave.create_user = AsyncMock(return_value=_remna_user())
    return sub_dao, user_dao, remnawave


@pytest.mark.asyncio
async def test_subscription_promocode_over_trial_clears_trial_flags(
    promo_deps, sample_user_dto, sample_subscription_dto, sample_plan_dto
):
    sub_dao, user_dao, remnawave = promo_deps
    sample_subscription_dto.is_trial = True
    sample_subscription_dto.disabled_by_channel_leave = True

    await _promocode(sub_dao, user_dao, remnawave, sample_plan_dto).system(
        ActivatePromocodeDto(code="X", user=sample_user_dto)
    )

    updated = sub_dao.update.await_args.args[0]
    assert updated.is_trial is False
    assert updated.disabled_by_channel_leave is False


@pytest.mark.asyncio
async def test_subscription_promocode_creating_subscription_consumes_trial(
    promo_deps, sample_user_dto, sample_plan_dto
):
    sub_dao, user_dao, remnawave = promo_deps
    sub_dao.get_current.return_value = None
    sample_user_dto.is_trial_available = True

    await _promocode(sub_dao, user_dao, remnawave, sample_plan_dto).system(
        ActivatePromocodeDto(code="X", user=sample_user_dto)
    )

    sub_dao.create.assert_awaited_once()
    assert user_dao.update.await_args.args[0].is_trial_available is False


# --------------------------------------------------------------------------- referral rewards


@pytest.mark.asyncio
async def test_extra_days_for_long_expired_subscription_reports_failure(
    sample_user_dto, sample_subscription_dto
):
    sample_subscription_dto.expire_at = datetime_now() - timedelta(days=30)
    user_dao = MagicMock()
    user_dao.get_by_id = AsyncMock(return_value=sample_user_dto)
    sub_dao = MagicMock()
    sub_dao.get_current = AsyncMock(return_value=sample_subscription_dto)
    publisher = MagicMock()
    publisher.publish = AsyncMock()
    add_duration = MagicMock()
    use_case = GiveReferrerReward(
        MagicMock(), user_dao, sub_dao, MagicMock(), publisher, MagicMock(), add_duration
    )
    reward = ReferralRewardDto(user_id=1, type=ReferralRewardType.EXTRA_DAYS, amount=7)

    await use_case.system(GiveReferrerRewardDto(user_id=1, reward=reward, referred_name="Bob"))

    assert isinstance(publisher.publish.await_args.args[0], ReferralRewardFailedEvent)
    add_duration.system.assert_not_called()


# --------------------------------------------------------------------------- ad links


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["with space", "кириллица", "x" * 62, ""])
async def test_ad_link_code_must_be_a_valid_start_parameter(uow, code):
    dao = MagicMock()
    dao.get_by_code = AsyncMock(return_value=None)
    dao.update = AsyncMock()

    with pytest.raises(ValueError):
        await UpdateAdLink(uow, dao).system(UpdateAdLinkDto(AdLinkDto(id=1, name="n", code=code)))
    if code:
        with pytest.raises(ValueError):
            await CreateAdLink(uow, dao, MagicMock()).system(CreateAdLinkDto(name="n", code=code))

    dao.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_ad_link_update_refuses_code_of_another_link(uow):
    dao = MagicMock()
    dao.get_by_code = AsyncMock(return_value=AdLinkDto(id=2, name="other", code="taken"))
    dao.update = AsyncMock()

    with pytest.raises(ValueError):
        await UpdateAdLink(uow, dao).system(
            UpdateAdLinkDto(AdLinkDto(id=1, name="n", code="taken"))
        )

    dao.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_ad_link_update_writes_all_fields_of_reloaded_dto(uow):
    dao = MagicMock()
    dao.get_by_code = AsyncMock(return_value=AdLinkDto(id=1, name="old", code="Promo_1"))
    dao.update = AsyncMock(side_effect=lambda link: link)
    # Rebuilt from dialog_data: no tracked changes.
    link = AdLinkDto(id=1, name="renamed", code="Promo_1", is_active=False)
    assert link.changed_data == {}

    await UpdateAdLink(uow, dao).system(UpdateAdLinkDto(link))

    written = dao.update.await_args.args[0].changed_data
    assert written["name"] == "renamed"
    assert written["code"] == "Promo_1"
    assert written["is_active"] is False


# --------------------------------------------------------------------------- email change


@pytest.mark.asyncio
async def test_change_email_refused_for_verified_email(uow, sample_user_dto):
    sample_user_dto.is_email_verified = True
    user_dao = MagicMock()
    user_dao.get_by_email = AsyncMock(return_value=None)
    user_dao.update = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await ChangeEmail(uow, user_dao)._execute(
            sample_user_dto, ChangeEmailDto(email="typo@example.com")
        )

    assert exc.value.status_code == 409
    user_dao.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_email_keeps_verified_flag_of_current_email(uow, sample_user_dto):
    sample_user_dto.email = "old@example.com"
    sample_user_dto.is_email_verified = False
    user_dao = MagicMock()
    user_dao.get_by_email = AsyncMock(return_value=None)
    user_dao.update = AsyncMock(side_effect=lambda user: user)

    updated = await ChangeEmail(uow, user_dao)._execute(
        sample_user_dto, ChangeEmailDto(email="new@example.com")
    )

    assert updated.pending_email == "new@example.com"
    assert updated.email == "old@example.com"
    assert "is_email_verified" not in updated.changed_data


# --------------------------------------------------------------------------- importer


def test_import_request_is_built_from_parsed_dataclass():
    squad = UUID("11111111-1111-1111-1111-111111111111")
    user = ExportedUserDto(
        username="rs_123456",
        telegram_id=123456,
        status=SubscriptionStatus.ACTIVE,
        expire_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        traffic_limit_bytes=0,
        hwid_device_limit=2,
        tag="IMPORTED",
    )

    request = build_create_user_request(user, [squad])

    assert request.username == "rs_123456"
    assert request.telegram_id == 123456
    assert request.hwid_device_limit == 2
    assert request.active_internal_squads == [squad]


def test_xui_delayed_start_client_is_not_expired():
    exporter = ExportUsersFromXui(MagicMock())

    exported = exporter._transform({"enable": True, "email": "123", "expiryTime": -2592000000})

    assert exported is not None
    assert exported.expire_at > datetime_now() + timedelta(days=29)


# --------------------------------------------------------------------------- misc


@pytest.mark.asyncio
async def test_invalid_int_gateway_setting_is_a_value_error(uow):
    gateway_dao = MagicMock()
    gateway_dao.get_by_id = AsyncMock(return_value=MagicMock(settings=MulenPayGatewaySettingsDto()))
    gateway_dao.update = AsyncMock()
    use_case = UpdatePaymentGatewaySettings(uow, gateway_dao, Retort(strict_coercion=False))

    with pytest.raises(ValueError):
        await use_case.system(UpdatePaymentGatewaySettingsDto(1, "shop_id", "abc"))

    gateway_dao.update.assert_not_awaited()


def test_double_click_confirmation_is_consumed():
    manager = MagicMock()
    manager.dialog_data = {}

    assert is_double_click(manager, "k") is False  # asks for confirmation
    assert is_double_click(manager, "k") is True  # confirmed, action runs
    assert is_double_click(manager, "k") is False  # a third click asks again


def test_webhook_error_from_recent_restart_is_reported():
    service = object.__new__(WebhookService)

    assert service._is_new_error(datetime_now() - timedelta(seconds=30)) is True
    assert service._is_new_error(datetime_now() - timedelta(hours=1)) is False
