"""Write paths must only touch panel users verified to belong to the bot user.

Regression tests for the incident where several bot users were bound to the same
`user_remna_id` and admin actions on one of them hit the real owner's panel user.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.use_cases.promocode.commands.activate import (
    ActivatePromocode,
    ActivatePromocodeDto,
)
from src.application.use_cases.remnawave.commands.management import (
    ReissueUserSubscription,
    ResetUserTraffic,
)
from src.application.use_cases.subscription.commands import set_plan as set_plan_module
from src.application.use_cases.subscription.commands.management import (
    AddSubscriptionDuration,
    AddSubscriptionDurationDto,
    DeleteSubscription,
    DisableTrialSubscription,
    ToggleSubscriptionStatus,
    UpdateTrafficLimit,
    UpdateTrafficLimitDto,
)
from src.application.use_cases.subscription.commands.purchase import (
    ActivateTrialSubscription,
    ActivateTrialSubscriptionDto,
    PurchaseSubscription,
    PurchaseSubscriptionDto,
)
from src.application.use_cases.subscription.commands.set_plan import (
    SetUserSubscription,
    SetUserSubscriptionDto,
)
from src.application.use_cases.user.commands.management import DeleteUser, DeleteUserDto
from src.core.enums import PromocodeRewardType, PurchaseType, SubscriptionStatus
from src.core.exceptions import RemnaUserBindingError

STORED_ID = 12345  # sample_subscription_dto.user_remna_id
OWNED_ID = 777


def _remna_user(id: int, telegram_id: int = 123456789) -> MagicMock:
    user = MagicMock()
    user.id = id
    user.telegram_id = telegram_id
    user.status = "ACTIVE"
    user.expire_at = datetime(2027, 1, 1, tzinfo=timezone.utc)
    user.subscription_url = f"https://sub.example.com/{id}"
    return user


@pytest.fixture
def uow():
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.commit = AsyncMock()
    return uow


@pytest.fixture
def user_dao(sample_user_dto):
    dao = MagicMock()
    dao.get_by_id = AsyncMock(return_value=sample_user_dto)
    dao.get_by_telegram_id = AsyncMock(return_value=sample_user_dto)
    dao.update = AsyncMock()
    dao.delete = AsyncMock()
    dao.set_trial_available = AsyncMock()
    dao.claim_trial = AsyncMock(return_value=True)
    dao.clear_current_subscription = AsyncMock()
    return dao


@pytest.fixture
def sub_dao(sample_subscription_dto):
    dao = MagicMock()
    dao.get_current = AsyncMock(return_value=sample_subscription_dto)
    dao.update = AsyncMock()
    dao.update_status = AsyncMock()
    dao.create = AsyncMock(side_effect=lambda subscription, user_id: subscription)
    dao.ensure_remna_id_available = AsyncMock()
    return dao


@pytest.fixture
def remnawave():
    remna = MagicMock()
    for name in (
        "resolve_user",
        "update_user",
        "create_user",
        "enable_user",
        "disable_user",
        "delete_user",
        "reset_traffic",
        "revoke_subscription",
        "get_users_by_telegram_id",
    ):
        setattr(remna, name, AsyncMock())
    return remna


def _forbidden_ids(remnawave: MagicMock) -> set:
    """IDs passed to any id-only panel primitive."""
    ids = set()
    for name in ("enable_user", "disable_user", "delete_user", "reset_traffic"):
        for call in getattr(remnawave, name).await_args_list:
            ids.add(call.args[0] if call.args else call.kwargs.get("id"))
    return ids


# --- PurchaseSubscription -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renew_persists_re_resolved_remna_id(
    uow, user_dao, sub_dao, remnawave, sample_user_dto, sample_subscription_dto, sample_plan_dto
):
    remnawave.update_user.return_value = _remna_user(OWNED_ID)
    transaction = MagicMock(plan_snapshot=sample_plan_dto, purchase_type=PurchaseType.RENEW)

    await PurchaseSubscription(uow, user_dao, sub_dao, remnawave).system(
        PurchaseSubscriptionDto(
            user=sample_user_dto, transaction=transaction, subscription=sample_subscription_dto
        )
    )

    remnawave.get_users_by_telegram_id.assert_not_awaited()
    saved = sub_dao.update.await_args.args[0]
    assert saved.user_remna_id == OWNED_ID
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("is_trial", [False, True])
async def test_change_or_trial_convert_creates_sub_with_returned_id(
    uow,
    user_dao,
    sub_dao,
    remnawave,
    sample_user_dto,
    sample_subscription_dto,
    sample_plan_dto,
    is_trial,
):
    sample_subscription_dto.is_trial = is_trial
    remnawave.update_user.return_value = _remna_user(OWNED_ID)
    transaction = MagicMock(plan_snapshot=sample_plan_dto, purchase_type=PurchaseType.CHANGE)

    await PurchaseSubscription(uow, user_dao, sub_dao, remnawave).system(
        PurchaseSubscriptionDto(
            user=sample_user_dto, transaction=transaction, subscription=sample_subscription_dto
        )
    )

    remnawave.create_user.assert_not_awaited()
    sub_dao.update_status.assert_awaited_once_with(
        subscription_id=sample_subscription_dto.id, status=SubscriptionStatus.DELETED
    )
    new_sub = sub_dao.create.await_args.kwargs["subscription"]
    assert new_sub.user_remna_id == OWNED_ID


@pytest.mark.asyncio
async def test_change_panel_failure_keeps_old_subscription(
    uow, user_dao, sub_dao, remnawave, sample_user_dto, sample_subscription_dto, sample_plan_dto
):
    remnawave.update_user.side_effect = RuntimeError("panel down")
    transaction = MagicMock(plan_snapshot=sample_plan_dto, purchase_type=PurchaseType.CHANGE)

    with pytest.raises(RuntimeError):
        await PurchaseSubscription(uow, user_dao, sub_dao, remnawave).system(
            PurchaseSubscriptionDto(
                user=sample_user_dto, transaction=transaction, subscription=sample_subscription_dto
            )
        )

    sub_dao.update_status.assert_not_awaited()
    sub_dao.create.assert_not_awaited()
    uow.commit.assert_not_awaited()


# --- ToggleSubscriptionStatus ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_toggle_does_not_touch_foreign_user_when_unresolved(
    uow, user_dao, sub_dao, remnawave
):
    remnawave.resolve_user.return_value = None

    with pytest.raises(RemnaUserBindingError):
        await ToggleSubscriptionStatus(uow, user_dao, sub_dao, remnawave).system(1)

    remnawave.disable_user.assert_not_awaited()
    remnawave.enable_user.assert_not_awaited()
    sub_dao.update_status.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_toggle_heals_and_uses_resolved_id(
    uow, user_dao, sub_dao, remnawave, sample_user_dto, sample_subscription_dto
):
    remnawave.resolve_user.return_value = _remna_user(OWNED_ID)

    status = await ToggleSubscriptionStatus(uow, user_dao, sub_dao, remnawave).system(1)

    assert status == SubscriptionStatus.DISABLED
    remnawave.resolve_user.assert_awaited_once_with(sample_user_dto, STORED_ID)
    remnawave.disable_user.assert_awaited_once_with(OWNED_ID)
    assert STORED_ID not in _forbidden_ids(remnawave)
    healed = sub_dao.update.await_args.args[0]
    assert healed.user_remna_id == OWNED_ID
    uow.commit.assert_awaited_once()


# --- DeleteSubscription / DeleteUser --------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_subscription_skips_panel_when_unresolved(uow, user_dao, sub_dao, remnawave):
    remnawave.resolve_user.return_value = None

    await DeleteSubscription(uow, user_dao, sub_dao, remnawave).system(1)

    remnawave.delete_user.assert_not_awaited()
    user_dao.clear_current_subscription.assert_awaited_once()
    sub_dao.update_status.assert_awaited_once()
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_subscription_deletes_resolved_id(uow, user_dao, sub_dao, remnawave):
    remnawave.resolve_user.return_value = _remna_user(OWNED_ID)

    await DeleteSubscription(uow, user_dao, sub_dao, remnawave).system(1)

    remnawave.delete_user.assert_awaited_once_with(OWNED_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize("resolved", [None, OWNED_ID])
async def test_delete_user_never_deletes_foreign_panel_user(
    uow, user_dao, sub_dao, remnawave, sample_user_dto, resolved
):
    remnawave.resolve_user.return_value = _remna_user(resolved) if resolved else None
    # Must not be consulted: a `[0]` of this list is how foreign users got picked.
    remnawave.get_users_by_telegram_id.return_value = [_remna_user(149, telegram_id=1)]

    await DeleteUser(uow, user_dao, sub_dao, remnawave).system(DeleteUserDto(user_id=1))

    remnawave.get_users_by_telegram_id.assert_not_awaited()
    remnawave.resolve_user.assert_awaited_once_with(sample_user_dto, STORED_ID)
    if resolved:
        remnawave.delete_user.assert_awaited_once_with(OWNED_ID)
    else:
        remnawave.delete_user.assert_not_awaited()
    user_dao.delete.assert_awaited_once()
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_user_without_subscription_resolves_by_owner(
    uow, user_dao, sub_dao, remnawave, sample_user_dto
):
    sub_dao.get_current.return_value = None
    remnawave.resolve_user.return_value = _remna_user(OWNED_ID)

    await DeleteUser(uow, user_dao, sub_dao, remnawave).system(DeleteUserDto(user_id=1))

    remnawave.resolve_user.assert_awaited_once_with(sample_user_dto, None)
    remnawave.delete_user.assert_awaited_once_with(OWNED_ID)


# --- Other update_user / id-only callers ----------------------------------------------------


@pytest.mark.asyncio
async def test_update_traffic_limit_persists_returned_id(uow, user_dao, sub_dao, remnawave):
    remnawave.update_user.return_value = _remna_user(OWNED_ID)

    await UpdateTrafficLimit(uow, user_dao, sub_dao, remnawave).system(
        UpdateTrafficLimitDto(user_id=1, traffic_limit=10)
    )

    assert sub_dao.update.await_args.args[0].user_remna_id == OWNED_ID


@pytest.mark.asyncio
async def test_disable_trial_uses_remnawave_abstraction_and_skips_unresolved(
    uow, user_dao, sub_dao, remnawave, sample_subscription_dto
):
    sample_subscription_dto.is_trial = True
    remnawave.resolve_user.return_value = None
    settings_dao = MagicMock()
    settings_dao.get = AsyncMock()
    use_case = DisableTrialSubscription(uow, settings_dao, user_dao, sub_dao, remnawave)
    use_case._guard_targets_channel = MagicMock(return_value=True)  # type: ignore[method-assign]

    result = await use_case.system(MagicMock(telegram_id=123456789))

    assert result is None
    remnawave.disable_user.assert_not_awaited()
    sub_dao.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_disable_trial_disables_resolved_id(
    uow, user_dao, sub_dao, remnawave, sample_subscription_dto
):
    sample_subscription_dto.is_trial = True
    remnawave.resolve_user.return_value = _remna_user(OWNED_ID)
    settings_dao = MagicMock()
    settings_dao.get = AsyncMock()
    use_case = DisableTrialSubscription(uow, settings_dao, user_dao, sub_dao, remnawave)
    use_case._guard_targets_channel = MagicMock(return_value=True)  # type: ignore[method-assign]

    await use_case.system(MagicMock(telegram_id=123456789))

    remnawave.disable_user.assert_awaited_once_with(OWNED_ID)
    saved = sub_dao.update.await_args.args[0]
    assert saved.user_remna_id == OWNED_ID
    assert saved.status == SubscriptionStatus.DISABLED


@pytest.mark.asyncio
async def test_reset_traffic_and_reissue_refuse_unresolved(uow, user_dao, sub_dao, remnawave):
    remnawave.resolve_user.return_value = None

    with pytest.raises(RemnaUserBindingError):
        await ResetUserTraffic(uow, user_dao, sub_dao, remnawave).system(1)
    with pytest.raises(RemnaUserBindingError):
        await ReissueUserSubscription(uow, user_dao, sub_dao, remnawave).system(1)

    remnawave.reset_traffic.assert_not_awaited()
    remnawave.revoke_subscription.assert_not_awaited()


@pytest.mark.asyncio
async def test_reissue_user_subscription_uses_resolved_id(uow, user_dao, sub_dao, remnawave):
    remnawave.resolve_user.return_value = _remna_user(OWNED_ID)

    await ReissueUserSubscription(uow, user_dao, sub_dao, remnawave).system(1)

    remnawave.revoke_subscription.assert_awaited_once_with(OWNED_ID)
    assert sub_dao.update.await_args.args[0].user_remna_id == OWNED_ID
    uow.commit.assert_awaited_once()


# --- ActivatePromocode ----------------------------------------------------------------------


def _promocode_use_case(uow, user_dao, sub_dao, remnawave, promo, plan=None):
    promocode_dao = MagicMock()
    promocode_dao.create_activation = AsyncMock()
    event_publisher = MagicMock()
    event_publisher.publish = AsyncMock()
    retort = MagicMock()
    retort.load = MagicMock(return_value=plan)
    return ActivatePromocode(
        uow=uow,
        promocode_dao=promocode_dao,
        user_dao=user_dao,
        subscription_dao=sub_dao,
        remnawave=remnawave,
        validate_promocode=AsyncMock(return_value=promo),
        event_publisher=event_publisher,
        retort=retort,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reward_type",
    [PromocodeRewardType.DURATION, PromocodeRewardType.TRAFFIC, PromocodeRewardType.DEVICES],
)
async def test_promocode_rewards_persist_returned_id(
    uow, user_dao, sub_dao, remnawave, sample_user_dto, reward_type
):
    remnawave.update_user.return_value = _remna_user(OWNED_ID)
    promo = MagicMock(reward_type=reward_type, reward=5, id=1, code="X", plan_snapshot=None)
    use_case = _promocode_use_case(uow, user_dao, sub_dao, remnawave, promo)

    await use_case.system(ActivatePromocodeDto(code="X", user=sample_user_dto))

    remnawave.get_users_by_telegram_id.assert_not_awaited()
    assert sub_dao.update.await_count == 1
    assert sub_dao.update.await_args.args[0].user_remna_id == OWNED_ID


@pytest.mark.asyncio
async def test_promocode_subscription_reward_persists_returned_id(
    uow, user_dao, sub_dao, remnawave, sample_user_dto, sample_plan_dto
):
    remnawave.update_user.return_value = _remna_user(OWNED_ID)
    promo = MagicMock(
        reward_type=PromocodeRewardType.SUBSCRIPTION,
        reward=None,
        id=1,
        code="X",
        plan_snapshot={"name": "plan"},
    )
    use_case = _promocode_use_case(uow, user_dao, sub_dao, remnawave, promo, sample_plan_dto)

    await use_case.system(ActivatePromocodeDto(code="X", user=sample_user_dto))

    assert sub_dao.update.await_args.args[0].user_remna_id == OWNED_ID


# --- Binding conflict is detected before the panel is mutated -------------------------------


def _conflict_cases(sample_user_dto, sample_subscription_dto, sample_plan_dto):
    def purchase(purchase_type):
        transaction = MagicMock(plan_snapshot=sample_plan_dto, purchase_type=purchase_type)
        return lambda uow, user_dao, sub_dao, remnawave: PurchaseSubscription(
            uow, user_dao, sub_dao, remnawave
        ).system(
            PurchaseSubscriptionDto(
                user=sample_user_dto, transaction=transaction, subscription=sample_subscription_dto
            )
        )

    def set_plan(uow, user_dao, sub_dao, remnawave):
        plan_dao = MagicMock()
        plan_dao.get_by_id = AsyncMock(return_value=sample_plan_dto)
        return SetUserSubscription(uow, user_dao, plan_dao, sub_dao, remnawave).system(
            SetUserSubscriptionDto(user_id=1, plan_id=1, duration=30)
        )

    def traffic(uow, user_dao, sub_dao, remnawave):
        return UpdateTrafficLimit(uow, user_dao, sub_dao, remnawave).system(
            UpdateTrafficLimitDto(user_id=1, traffic_limit=10)
        )

    def duration(uow, user_dao, sub_dao, remnawave):
        return AddSubscriptionDuration(uow, user_dao, sub_dao, remnawave).system(
            AddSubscriptionDurationDto(user_id=1, days=5)
        )

    def promocode(uow, user_dao, sub_dao, remnawave):
        promo = MagicMock(
            reward_type=PromocodeRewardType.DURATION, reward=5, id=1, code="X", plan_snapshot=None
        )
        use_case = _promocode_use_case(uow, user_dao, sub_dao, remnawave, promo)
        return use_case.system(ActivatePromocodeDto(code="X", user=sample_user_dto))

    return {
        "renew": purchase(PurchaseType.RENEW),
        "change": purchase(PurchaseType.CHANGE),
        "set_plan": set_plan,
        "traffic_limit": traffic,
        "add_duration": duration,
        "promocode": promocode,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["renew", "change", "set_plan", "traffic_limit", "add_duration", "promocode"]
)
async def test_binding_conflict_blocks_panel_mutation(
    monkeypatch,
    uow,
    user_dao,
    sub_dao,
    remnawave,
    sample_user_dto,
    sample_subscription_dto,
    sample_plan_dto,
    case,
):
    monkeypatch.setattr(
        set_plan_module.PlanSnapshotDto, "from_plan", MagicMock(return_value=sample_plan_dto)
    )
    remnawave.resolve_user.return_value = _remna_user(OWNED_ID)
    sub_dao.ensure_remna_id_available.side_effect = RemnaUserBindingError("bound to user 2")
    run = _conflict_cases(sample_user_dto, sample_subscription_dto, sample_plan_dto)[case]

    with pytest.raises(RemnaUserBindingError):
        await run(uow, user_dao, sub_dao, remnawave)

    sub_dao.ensure_remna_id_available.assert_awaited_once_with(OWNED_ID, sample_user_dto.id)
    remnawave.update_user.assert_not_awaited()
    remnawave.create_user.assert_not_awaited()
    sub_dao.update_status.assert_not_awaited()
    sub_dao.create.assert_not_awaited()
    uow.commit.assert_not_awaited()


# --- create_user callers check the binding before the panel is mutated ----------------------


def _create_user_cases(sample_user_dto, sample_plan_dto):
    def new_purchase(uow, user_dao, sub_dao, remnawave):
        transaction = MagicMock(plan_snapshot=sample_plan_dto, purchase_type=PurchaseType.NEW)
        return PurchaseSubscription(uow, user_dao, sub_dao, remnawave).system(
            PurchaseSubscriptionDto(
                user=sample_user_dto, transaction=transaction, subscription=None
            )
        )

    def trial(uow, user_dao, sub_dao, remnawave):
        sample_user_dto.is_trial_available = True
        sub_dao.get_current.return_value = None
        event_publisher = MagicMock()
        event_publisher.publish = AsyncMock()
        return ActivateTrialSubscription(uow, user_dao, sub_dao, remnawave, event_publisher).system(
            ActivateTrialSubscriptionDto(user=sample_user_dto, plan=sample_plan_dto)
        )

    def promocode_subscription(uow, user_dao, sub_dao, remnawave):
        sub_dao.get_current.return_value = None
        promo = MagicMock(
            reward_type=PromocodeRewardType.SUBSCRIPTION,
            reward=None,
            id=1,
            code="X",
            plan_snapshot={"name": "plan"},
        )
        use_case = _promocode_use_case(uow, user_dao, sub_dao, remnawave, promo, sample_plan_dto)
        return use_case.system(ActivatePromocodeDto(code="X", user=sample_user_dto))

    return {"new": new_purchase, "trial": trial, "promocode_subscription": promocode_subscription}


CREATE_USER_CASES = ["new", "trial", "promocode_subscription"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CREATE_USER_CASES)
async def test_create_user_flows_block_on_binding_conflict(
    uow, user_dao, sub_dao, remnawave, sample_user_dto, sample_plan_dto, case
):
    remnawave.resolve_user.return_value = _remna_user(OWNED_ID)
    sub_dao.ensure_remna_id_available.side_effect = RemnaUserBindingError("bound to user 2")
    run = _create_user_cases(sample_user_dto, sample_plan_dto)[case]

    with pytest.raises(RemnaUserBindingError):
        await run(uow, user_dao, sub_dao, remnawave)

    remnawave.resolve_user.assert_awaited_once_with(sample_user_dto, None)
    sub_dao.ensure_remna_id_available.assert_awaited_once_with(OWNED_ID, sample_user_dto.id)
    remnawave.create_user.assert_not_awaited()
    remnawave.update_user.assert_not_awaited()
    sub_dao.create.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CREATE_USER_CASES)
@pytest.mark.parametrize("owned", [True, False])
async def test_create_user_flows_persist_created_id(
    uow, user_dao, sub_dao, remnawave, sample_user_dto, sample_plan_dto, case, owned
):
    remnawave.resolve_user.return_value = _remna_user(OWNED_ID) if owned else None
    remnawave.create_user.return_value = _remna_user(OWNED_ID)
    run = _create_user_cases(sample_user_dto, sample_plan_dto)[case]

    await run(uow, user_dao, sub_dao, remnawave)

    if owned:
        sub_dao.ensure_remna_id_available.assert_awaited_once_with(OWNED_ID, sample_user_dto.id)
    else:
        sub_dao.ensure_remna_id_available.assert_not_awaited()
    remnawave.create_user.assert_awaited_once()
    assert sub_dao.create.await_args.kwargs["subscription"].user_remna_id == OWNED_ID
    uow.commit.assert_awaited_once()


# --- SetUserSubscription --------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("has_subscription", [True, False])
async def test_set_user_subscription_uses_returned_id(
    monkeypatch,
    uow,
    user_dao,
    sub_dao,
    remnawave,
    sample_user_dto,
    sample_plan_dto,
    has_subscription,
):
    monkeypatch.setattr(
        set_plan_module.PlanSnapshotDto, "from_plan", MagicMock(return_value=sample_plan_dto)
    )
    if not has_subscription:
        sub_dao.get_current.return_value = None
    remnawave.resolve_user.return_value = _remna_user(STORED_ID) if has_subscription else None
    remnawave.update_user.return_value = _remna_user(OWNED_ID)
    plan_dao = MagicMock()
    plan_dao.get_by_id = AsyncMock(return_value=sample_plan_dto)

    await SetUserSubscription(uow, user_dao, plan_dao, sub_dao, remnawave).system(
        SetUserSubscriptionDto(user_id=1, plan_id=1, duration=30)
    )

    remnawave.get_users_by_telegram_id.assert_not_awaited()
    remnawave.create_user.assert_not_awaited()
    remnawave.resolve_user.assert_awaited_once_with(
        sample_user_dto, STORED_ID if has_subscription else None
    )
    assert remnawave.update_user.await_args.kwargs["id"] == (STORED_ID if has_subscription else 0)
    if has_subscription:
        sub_dao.ensure_remna_id_available.assert_awaited_once_with(STORED_ID, sample_user_dto.id)
    else:
        sub_dao.ensure_remna_id_available.assert_not_awaited()
    new_sub = sub_dao.create.await_args.args[0]
    assert new_sub.user_remna_id == OWNED_ID
    assert sub_dao.update_status.await_count == (1 if has_subscription else 0)
