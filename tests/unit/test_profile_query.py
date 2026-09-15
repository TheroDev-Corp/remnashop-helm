from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.dto import UserDto
from src.application.use_cases.user.queries.profile import (
    GetUserDevices,
    GetUserProfileSubscription,
)
from src.core.enums import Role

SYSTEM = UserDto(id=0, name="System", role=Role.SYSTEM)


@pytest.fixture
def deps(sample_user_dto, sample_subscription_dto):
    uow = MagicMock()
    uow.commit = AsyncMock()
    user_dao = MagicMock()
    user_dao.get_by_id = AsyncMock(return_value=sample_user_dto)
    sub_dao = MagicMock()
    sub_dao.get_current = AsyncMock(return_value=sample_subscription_dto)
    sub_dao.update = AsyncMock()
    remnawave = MagicMock()
    remnawave.resolve_user = AsyncMock(return_value=None)
    remnawave.get_users_by_telegram_id = AsyncMock(return_value=[])
    remnawave.get_user_by_id = AsyncMock(return_value=None)
    use_case = GetUserProfileSubscription(
        uow=uow,
        user_dao=user_dao,
        subscription_dao=sub_dao,
        remnawave=remnawave,
        remnawave_sdk=MagicMock(),
    )
    return use_case, uow, sub_dao, remnawave


def _remna_user(id: int) -> MagicMock:
    remna_user = MagicMock()
    remna_user.id = id
    remna_user.last_connected_node_uuid = None
    remna_user.external_squad_uuid = None
    return remna_user


@pytest.mark.asyncio
async def test_profile_heals_from_resolve_user(deps, sample_user_dto, sample_subscription_dto):
    use_case, uow, sub_dao, remnawave = deps
    sample_subscription_dto.user_remna_id = 149
    remnawave.resolve_user.return_value = _remna_user(500)

    result = await use_case(SYSTEM, sample_user_dto.id)

    remnawave.resolve_user.assert_awaited_once_with(sample_user_dto, 149)
    remnawave.get_users_by_telegram_id.assert_not_awaited()
    sub_dao.update.assert_awaited_once()
    assert sub_dao.update.await_args.args[0].user_remna_id == 500
    uow.commit.assert_awaited_once()
    assert result.remna_user.id == 500


@pytest.mark.asyncio
async def test_profile_does_not_heal_when_ids_match(deps, sample_user_dto, sample_subscription_dto):
    use_case, _, sub_dao, remnawave = deps
    remnawave.resolve_user.return_value = _remna_user(sample_subscription_dto.user_remna_id)

    await use_case(SYSTEM, sample_user_dto.id)

    sub_dao.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_devices_fall_back_to_empty_on_panel_error(
    deps, sample_user_dto, sample_subscription_dto
):
    _, _, sub_dao, remnawave = deps
    user_dao = MagicMock()
    user_dao.get_by_id = AsyncMock(return_value=sample_user_dto)
    remnawave.resolve_user.side_effect = RuntimeError("panel returned 502")
    remnawave.get_devices = AsyncMock()

    result = await GetUserDevices(user_dao, sub_dao, remnawave).system(sample_user_dto.id)

    assert result.devices == []
    assert result.current_count == 0
    assert result.max_count == sample_subscription_dto.device_limit
    remnawave.get_devices.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_never_heals_without_resolved_owner(deps, sample_user_dto):
    use_case, _, sub_dao, remnawave = deps
    remnawave.resolve_user.return_value = None

    # The window still renders (remna_user=None) so the admin can delete the orphaned subscription.
    result = await use_case(SYSTEM, sample_user_dto.id)

    assert result.remna_user is None
    assert result.formatted_internal_squads is None
    sub_dao.update.assert_not_awaited()
