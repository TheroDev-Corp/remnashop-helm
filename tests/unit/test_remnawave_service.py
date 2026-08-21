from unittest.mock import AsyncMock, MagicMock

import pytest
from packaging.version import Version
from remnapy.exceptions import ConflictError, NotFoundError
from remnapy.models import GetMetadataResponseDto, UserResponseDto

from src.infrastructure.services.remnawave import RemnawaveImpl


@pytest.fixture
def mock_sdk():
    sdk = MagicMock()
    sdk._client = AsyncMock()
    sdk.users = MagicMock()
    sdk.hwid = MagicMock()
    sdk.system = MagicMock()
    return sdk


@pytest.fixture
def remnawave_service(mock_sdk):
    service = RemnawaveImpl(sdk=mock_sdk)
    return service


@pytest.mark.asyncio
async def test_try_connection_v3_success(remnawave_service, mock_sdk):
    meta_response = MagicMock(spec=GetMetadataResponseDto)
    meta_response.version = "3.2.3"
    mock_sdk.system.get_metadata = AsyncMock(return_value=meta_response)

    version = await remnawave_service.try_connection()
    assert version == Version("3.2.3")
    assert remnawave_service.is_v3 is True


@pytest.mark.asyncio
async def test_try_connection_v2_success(remnawave_service, mock_sdk):
    meta_response = MagicMock(spec=GetMetadataResponseDto)
    meta_response.version = "2.8.1"
    mock_sdk.system.get_metadata = AsyncMock(return_value=meta_response)

    version = await remnawave_service.try_connection()
    assert version == Version("2.8.1")
    assert remnawave_service.is_v3 is False


def test_normalize_user_dict_v3_minimal():
    v3_user = {
        "id": 101,
        "username": "test_v3",
        "status": "ACTIVE",
        "trafficLimitStrategy": "NO_RESET",
        "trafficLimitBytes": 1000000,
        "usedTrafficBytes": 500,
        "lifetimeUsedTrafficBytes": 1500,
        "vlessUuid": "11111111-2222-3333-4444-555555555555",
        "expireAt": "2026-12-31T23:59:59Z",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-02T00:00:00Z",
    }

    norm = RemnawaveImpl._normalize_user_dict(v3_user)
    assert norm["id"] == 101
    assert norm["uuid"] == "11111111-2222-3333-4444-555555555555"
    assert norm["shortUuid"] == "101"
    assert norm["userTraffic"]["usedTrafficBytes"] == 500
    assert norm["userTraffic"]["lifetimeUsedTrafficBytes"] == 1500

    user_dto = UserResponseDto.model_validate(norm)
    assert user_dto.id == 101
    assert str(user_dto.uuid) == "11111111-2222-3333-4444-555555555555"


def test_normalize_user_dict_without_vless():
    v3_user = {
        "id": 42,
        "username": "numeric_user",
        "status": "ACTIVE",
    }

    norm = RemnawaveImpl._normalize_user_dict(v3_user)
    assert norm["id"] == 42
    assert norm["uuid"] == "00000000-0000-0000-0000-000000000042"
    assert norm["trafficLimitStrategy"] == "NO_RESET"
    assert norm["trafficLimitBytes"] == 0

    user_dto = UserResponseDto.model_validate(norm)
    assert user_dto.id == 42


@pytest.mark.asyncio
async def test_create_user_v3(remnawave_service, mock_sdk, sample_user_dto, sample_plan_dto):
    remnawave_service._panel_version = Version("3.2.0")

    fake_response = MagicMock()
    fake_response.status_code = 201
    fake_response.json.return_value = {
        "id": 555,
        "username": sample_user_dto.remna_name,
        "status": "ACTIVE",
        "vlessUuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "expireAt": "2026-12-31T23:59:59Z",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    }
    mock_sdk._client.post = AsyncMock(return_value=fake_response)

    user_res = await remnawave_service.create_user(sample_user_dto, sample_plan_dto)
    assert user_res.id == 555
    mock_sdk._client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_user_v3_conflict(
    remnawave_service,
    mock_sdk,
    sample_user_dto,
    sample_plan_dto,
):
    remnawave_service._panel_version = Version("3.2.0")

    fake_response = MagicMock()
    fake_response.status_code = 409
    mock_sdk._client.post = AsyncMock(return_value=fake_response)

    with pytest.raises(ConflictError):
        await remnawave_service.create_user(sample_user_dto, sample_plan_dto)


@pytest.mark.asyncio
async def test_update_user_v3(remnawave_service, mock_sdk, sample_user_dto, sample_plan_dto):
    remnawave_service._panel_version = Version("3.2.0")

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "id": 555,
        "username": sample_user_dto.remna_name,
        "status": "ACTIVE",
        "expireAt": "2026-12-31T23:59:59Z",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    }
    mock_sdk._client.patch = AsyncMock(return_value=fake_response)

    user_res = await remnawave_service.update_user(sample_user_dto, id=555, plan=sample_plan_dto)
    assert user_res.id == 555
    mock_sdk._client.patch.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_user_v3_not_found(
    remnawave_service,
    mock_sdk,
    sample_user_dto,
    sample_plan_dto,
):
    remnawave_service._panel_version = Version("3.2.0")

    fake_response = MagicMock()
    fake_response.status_code = 404
    mock_sdk._client.patch = AsyncMock(return_value=fake_response)

    with pytest.raises(NotFoundError):
        await remnawave_service.update_user(sample_user_dto, id=999, plan=sample_plan_dto)


@pytest.mark.asyncio
async def test_enable_and_disable_user_v3(remnawave_service, mock_sdk):
    remnawave_service._panel_version = Version("3.2.0")

    fake_response = MagicMock()
    fake_response.status_code = 200
    mock_sdk._client.post = AsyncMock(return_value=fake_response)

    await remnawave_service.enable_user(id=100)
    mock_sdk._client.post.assert_awaited_with("/users/100/actions/enable")

    await remnawave_service.disable_user(id=100)
    mock_sdk._client.post.assert_awaited_with("/users/100/actions/disable")


@pytest.mark.asyncio
async def test_delete_user_v3(remnawave_service, mock_sdk):
    remnawave_service._panel_version = Version("3.2.0")

    fake_response = MagicMock()
    fake_response.status_code = 200
    mock_sdk._client.delete = AsyncMock(return_value=fake_response)

    await remnawave_service.delete_user(id=100)
    mock_sdk._client.delete.assert_awaited_with("/users/100")


@pytest.mark.asyncio
async def test_get_user_by_id_v3(remnawave_service, mock_sdk):
    remnawave_service._panel_version = Version("3.2.0")

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "response": {
            "id": 100,
            "username": "user100",
            "status": "ACTIVE",
            "expireAt": "2026-12-31T23:59:59Z",
        }
    }
    mock_sdk._client.get = AsyncMock(return_value=fake_response)

    user = await remnawave_service.get_user_by_id(id=100)
    assert user is not None
    assert user.id == 100
    mock_sdk._client.get.assert_awaited_with("/users/100")


@pytest.mark.asyncio
async def test_get_user_by_id_v2_success(remnawave_service, mock_sdk):
    remnawave_service._panel_version = Version("2.8.1")

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "response": {
            "id": 147,
            "username": "user147",
            "status": "ACTIVE",
            "expireAt": "2026-12-31T23:59:59Z",
        }
    }
    mock_sdk._client.get = AsyncMock(return_value=fake_response)

    user = await remnawave_service.get_user_by_id(id=147)
    assert user is not None
    assert user.id == 147
    mock_sdk._client.get.assert_awaited_with("/users/by-id/147")


@pytest.mark.asyncio
async def test_get_user_by_id_v2_not_found_with_html_magi_error(remnawave_service, mock_sdk):
    remnawave_service._panel_version = Version("2.8.1")

    fake_response = MagicMock()
    fake_response.status_code = 404
    fake_response.text = "<script>window.__MAGI_CODE__='404';</script>"
    mock_sdk._client.get = AsyncMock(return_value=fake_response)

    user = await remnawave_service.get_user_by_id(id=999)
    assert user is None


@pytest.mark.asyncio
async def test_get_users_by_telegram_id_v2_success(remnawave_service, mock_sdk):
    remnawave_service._panel_version = Version("2.8.1")

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "response": [
            {
                "id": 147,
                "username": "user147",
                "telegramId": 628126350,
                "status": "ACTIVE",
                "expireAt": "2026-12-31T23:59:59Z",
            }
        ]
    }
    mock_sdk._client.get = AsyncMock(return_value=fake_response)

    users = await remnawave_service.get_users_by_telegram_id(telegram_id=628126350)
    assert len(users) == 1
    assert users[0].id == 147
    assert users[0].telegram_id == 628126350
