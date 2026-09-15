"""Public subscription endpoints must only read or mutate the panel user owned by the caller."""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from dishka import Provider, Scope, make_async_container
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.application.common import Remnawave
from src.application.common.dao import SubscriptionDao
from src.application.use_cases.remnawave.commands.management import (
    DeleteUserAllDevices,
    DeleteUserDevice,
    ReissueSubscription,
)
from src.core.exceptions import CooldownError, RemnaUserBindingError
from src.web.endpoints.public._common import _get_current_user
from src.web.endpoints.public.subscription import router

STORED_ID = 12345  # sample_subscription_dto.user_remna_id
OWNED_ID = 777


@pytest.fixture
def mocks(sample_subscription_dto) -> dict[type, Any]:
    subscription_dao = MagicMock()
    subscription_dao.get_current = AsyncMock(return_value=sample_subscription_dto)
    remnawave = MagicMock()
    remnawave.resolve_user = AsyncMock(return_value=None)
    remnawave.get_devices = AsyncMock(return_value=[])
    return {
        SubscriptionDao: subscription_dao,
        Remnawave: remnawave,
        DeleteUserDevice: AsyncMock(return_value=True),
        DeleteUserAllDevices: AsyncMock(return_value=None),
        ReissueSubscription: AsyncMock(return_value=None),
    }


def _constant(value: Any):
    # dishka needs a hint-complete factory; a zero-arg closure has nothing to annotate.
    return lambda: value


@pytest.fixture
def client(mocks, sample_user_dto):
    provider = Provider(scope=Scope.APP)
    for dependency, mock in mocks.items():
        provider.provide(_constant(mock), provides=dependency)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_get_current_user] = lambda: sample_user_dto
    container = make_async_container(provider)
    setup_dishka(container, app)

    with TestClient(app) as test_client:
        yield test_client


def _remna_user(id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        used_traffic_bytes=1024,
        lifetime_used_traffic_bytes=4096,
        online_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


# --- GET /subscription/current --------------------------------------------------------------


def test_current_without_subscription_returns_null(client, mocks):
    mocks[SubscriptionDao].get_current.return_value = None

    response = client.get("/subscription/current")

    assert response.status_code == 200
    assert response.json() is None
    mocks[Remnawave].resolve_user.assert_not_awaited()


def test_current_uses_owned_panel_user_stats(client, mocks, sample_user_dto):
    mocks[Remnawave].resolve_user.return_value = _remna_user(OWNED_ID)

    response = client.get("/subscription/current")

    assert response.status_code == 200
    body = response.json()
    assert body["used_traffic_bytes"] == 1024
    assert body["lifetime_used_traffic_bytes"] == 4096
    assert body["online_at"] is not None
    mocks[Remnawave].resolve_user.assert_awaited_once_with(sample_user_dto, STORED_ID)


@pytest.mark.parametrize("panel", ["unresolved", "error"])
def test_current_without_owned_panel_user_omits_stats(client, mocks, panel):
    if panel == "error":
        mocks[Remnawave].resolve_user.side_effect = RuntimeError("panel down")

    response = client.get("/subscription/current")

    assert response.status_code == 200
    body = response.json()
    assert body["user_remna_id"] == str(STORED_ID)
    assert body["used_traffic_bytes"] is None
    assert body["lifetime_used_traffic_bytes"] is None
    assert body["online_at"] is None


# --- GET /subscription/devices --------------------------------------------------------------


def test_devices_uses_resolved_id(client, mocks, sample_user_dto, sample_subscription_dto):
    mocks[Remnawave].resolve_user.return_value = _remna_user(OWNED_ID)
    mocks[Remnawave].get_devices.return_value = [
        SimpleNamespace(
            hwid="hwid-1",
            platform="ios",
            device_model="iPhone",
            os_version="18.0",
            user_agent="Shadowrocket",
        )
    ]

    response = client.get("/subscription/devices")

    assert response.status_code == 200
    body = response.json()
    assert [d["hwid"] for d in body["devices"]] == ["hwid-1"]
    assert body["current_count"] == 1
    assert body["max_count"] == sample_subscription_dto.device_limit
    mocks[Remnawave].resolve_user.assert_awaited_once_with(sample_user_dto, STORED_ID)
    mocks[Remnawave].get_devices.assert_awaited_once_with(OWNED_ID)


@pytest.mark.parametrize("panel", ["unresolved", "error"])
def test_devices_without_owned_panel_user_returns_empty_list(client, mocks, panel):
    if panel == "error":
        mocks[Remnawave].resolve_user.side_effect = RuntimeError("panel down")

    response = client.get("/subscription/devices")

    assert response.status_code == 200
    assert response.json()["devices"] == []
    assert response.json()["current_count"] == 0
    # The stored id must never be used to read somebody else's devices.
    mocks[Remnawave].get_devices.assert_not_awaited()


def test_devices_without_subscription_returns_404(client, mocks):
    mocks[SubscriptionDao].get_current.return_value = None

    response = client.get("/subscription/devices")

    assert response.status_code == 404
    mocks[Remnawave].resolve_user.assert_not_awaited()


# --- DELETE /subscription/devices/{hwid} ----------------------------------------------------


def test_delete_device_success(client, mocks, sample_user_dto):
    response = client.delete("/subscription/devices/hwid-1")

    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    actor, dto = mocks[DeleteUserDevice].await_args.args
    assert actor == sample_user_dto
    assert dto.user_id == sample_user_dto.id
    assert dto.hwid == "hwid-1"


COMMAND_ERRORS = [
    (ValueError("no subscription"), 409),
    (RemnaUserBindingError("not owned"), 409),
    (CooldownError(datetime(2026, 9, 15, tzinfo=timezone.utc)), 429),
]


@pytest.mark.parametrize(("error", "status_code"), COMMAND_ERRORS)
def test_delete_device_maps_errors(client, mocks, error, status_code):
    mocks[DeleteUserDevice].side_effect = error

    response = client.delete("/subscription/devices/hwid-1")

    assert response.status_code == status_code
    assert response.json()["detail"] == str(error)


# --- DELETE /subscription/devices -----------------------------------------------------------


def test_delete_all_devices_success(client, mocks, sample_user_dto):
    response = client.delete("/subscription/devices")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    mocks[DeleteUserAllDevices].assert_awaited_once_with(sample_user_dto)


@pytest.mark.parametrize(("error", "status_code"), COMMAND_ERRORS)
def test_delete_all_devices_maps_errors(client, mocks, error, status_code):
    mocks[DeleteUserAllDevices].side_effect = error

    response = client.delete("/subscription/devices")

    assert response.status_code == status_code


# --- POST /subscription/reissue -------------------------------------------------------------


def test_reissue_success(client, mocks, sample_user_dto):
    response = client.post("/subscription/reissue")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    mocks[ReissueSubscription].assert_awaited_once_with(sample_user_dto)


@pytest.mark.parametrize(("error", "status_code"), COMMAND_ERRORS)
def test_reissue_maps_errors(client, mocks, error, status_code):
    mocks[ReissueSubscription].side_effect = error

    response = client.post("/subscription/reissue")

    assert response.status_code == status_code
