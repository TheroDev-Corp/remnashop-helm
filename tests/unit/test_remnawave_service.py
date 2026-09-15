import json
from typing import Any, Callable, Optional, Union
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from packaging.version import Version
from remnapy.exceptions import ConflictError, NotFoundError
from remnapy.models import GetMetadataResponseDto, UserResponseDto

from src.core.exceptions import RemnaUserBindingError
from src.infrastructure.services.remnawave import RemnawaveImpl

PANEL = "https://panel.example"

Handler = Union[
    httpx.Response,
    list[httpx.Response],
    Callable[[Optional[dict[str, Any]], Optional[dict[str, Any]]], httpx.Response],
]


def _resp(status: int, body: Any = None, method: str = "GET", path: str = "/") -> httpx.Response:
    request = httpx.Request(method, f"{PANEL}{path}")
    if body is None:
        return httpx.Response(status, request=request)
    return httpx.Response(status, json=body, request=request)


def _user(
    id: int,
    telegram_id: Optional[int] = None,
    username: Optional[str] = None,
    email: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "shortUuid": f"short{id}",
        "username": username or f"panel_user_{id}",
        "status": "ACTIVE",
        "trafficLimitBytes": 0,
        "trafficLimitStrategy": "NO_RESET",
        "expireAt": "2099-12-31T23:59:59.000Z",
        "telegramId": telegram_id,
        "email": email,
        "createdAt": "2026-01-01T00:00:00.000Z",
        "updatedAt": "2026-01-01T00:00:00.000Z",
    }


def _one(user: dict[str, Any]) -> dict[str, Any]:
    return {"response": user}


def _stream(users: list[dict[str, Any]], next_cursor: Optional[str] = None) -> dict[str, Any]:
    return {
        "response": {
            "users": users,
            "nextCursor": next_cursor,
            "hasMore": next_cursor is not None,
        }
    }


class FakePanel:
    """Minimal router standing in for the Remnawave HTTP client; records every call."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Handler] = {}
        self.calls: list[tuple[str, str, Optional[dict[str, Any]], Optional[dict[str, Any]]]] = []

    def on(self, method: str, path: str, handler: Handler) -> None:
        self.routes[(method, path)] = handler

    def _dispatch(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> httpx.Response:
        self.calls.append((method, path, params, json))
        handler = self.routes.get((method, path))
        if handler is None:
            return _resp(404, {"message": "Not found", "errorCode": "E000"}, method, path)
        if isinstance(handler, list):
            return handler.pop(0)
        if isinstance(handler, httpx.Response):
            return handler
        return handler(params, json)

    async def get(self, path: str, params: Optional[dict[str, Any]] = None) -> httpx.Response:
        return self._dispatch("GET", path, params=params)

    async def post(self, path: str, json: Optional[dict[str, Any]] = None) -> httpx.Response:
        return self._dispatch("POST", path, json=json)

    async def patch(self, path: str, json: Optional[dict[str, Any]] = None) -> httpx.Response:
        return self._dispatch("PATCH", path, json=json)

    async def delete(self, path: str) -> httpx.Response:
        return self._dispatch("DELETE", path)

    def paths(self, method: Optional[str] = None) -> list[str]:
        return [p for m, p, _, _ in self.calls if method is None or m == method]


@pytest.fixture
def panel() -> FakePanel:
    return FakePanel()


@pytest.fixture
def mock_sdk(panel):
    sdk = MagicMock()
    sdk._client = panel
    sdk.system = MagicMock()
    return sdk


@pytest.fixture
def service(mock_sdk) -> RemnawaveImpl:
    return RemnawaveImpl(sdk=mock_sdk)


# --------------------------------------------------------------------------- try_connection


async def test_try_connection_v3_success(service, mock_sdk):
    meta = MagicMock(spec=GetMetadataResponseDto)
    meta.version = "3.2.3"
    mock_sdk.system.get_metadata = AsyncMock(return_value=meta)

    assert await service.try_connection() == Version("3.2.3")


@pytest.mark.parametrize("version", ["2.7.0", "2.8.1"])
async def test_try_connection_rejects_v2(service, mock_sdk, version):
    meta = MagicMock(spec=GetMetadataResponseDto)
    meta.version = version
    mock_sdk.system.get_metadata = AsyncMock(return_value=meta)

    with pytest.raises(ValueError, match="not compatible"):
        await service.try_connection()


# --------------------------------------------------------------------------- normalization


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
    assert norm["uuid"] == "11111111-2222-3333-4444-555555555555"
    assert norm["shortUuid"] == "101"
    assert norm["userTraffic"]["usedTrafficBytes"] == 500

    user_dto = UserResponseDto.model_validate(norm)
    assert user_dto.id == 101


def test_normalize_user_dict_without_vless():
    norm = RemnawaveImpl._normalize_user_dict({"id": 42, "username": "u", "status": "ACTIVE"})
    assert norm["uuid"] == "00000000-0000-0000-0000-000000000042"
    assert UserResponseDto.model_validate(norm).id == 42


def test_parse_user_response_keeps_numeric_telegram_id(service):
    remna_user = service._parse_user_response(_one(_user(1, telegram_id=5123456789)))
    assert remna_user.telegram_id == 5123456789
    assert isinstance(remna_user.telegram_id, int)


# --------------------------------------------------------------------------- telegram_id lookup


async def test_get_users_by_telegram_id_uses_exact_stream_filter(service, panel):
    panel.on("GET", "/users/stream", _resp(200, _stream([_user(7, telegram_id=123456789)])))

    users = await service.get_users_by_telegram_id(123456789)

    assert [u.id for u in users] == [7]
    _, path, params, _ = panel.calls[0]
    assert path == "/users/stream"
    assert params["telegramId"] == "123456789"


async def test_get_users_by_telegram_id_drops_partial_matches(service, panel):
    panel.on(
        "GET",
        "/users/stream",
        _resp(
            200,
            _stream(
                [
                    _user(1, telegram_id=5123456789),
                    _user(2, telegram_id=123456789),
                    _user(3, telegram_id=None),
                ]
            ),
        ),
    )

    users = await service.get_users_by_telegram_id(123456789)
    assert [u.id for u in users] == [2]


async def test_get_users_by_telegram_id_paginates_stream(service, panel):
    pages = [
        _resp(200, _stream([_user(1, telegram_id=42)], next_cursor="1")),
        _resp(200, _stream([_user(2, telegram_id=42)])),
    ]
    panel.on("GET", "/users/stream", pages)

    users = await service.get_users_by_telegram_id(42)

    assert [u.id for u in users] == [1, 2]
    assert "cursor" not in panel.calls[0][2]
    assert panel.calls[1][2]["cursor"] == "1"


@pytest.mark.parametrize("unavailable", [403, 404])
async def test_get_users_by_telegram_id_falls_back_to_like_filter(service, panel, unavailable):
    panel.on("GET", "/users/stream", _resp(unavailable, {"message": "x"}))

    size = 100
    first_page = [_user(i, telegram_id=int(f"9{i}123456789")) for i in range(size - 1)]
    first_page.append(_user(500, telegram_id=123456789))

    def users_handler(params, _json):
        assert json.loads(params["filters"]) == [{"id": "telegramId", "value": "123456789"}]
        if params["start"] == 0:
            assert params["size"] == size
            return _resp(200, {"response": {"users": first_page, "total": size + 2}})
        assert params["start"] == size
        return _resp(
            200,
            {
                "response": {
                    "users": [_user(501, telegram_id=123456789), _user(502, telegram_id=1234567)],
                    "total": size + 2,
                }
            },
        )

    panel.on("GET", "/users", users_handler)

    users = await service.get_users_by_telegram_id(123456789)
    assert [u.id for u in users] == [500, 501]


async def test_get_users_by_telegram_id_raises_on_server_error(service, panel):
    panel.on("GET", "/users/stream", _resp(500, {"message": "boom"}))

    with pytest.raises(httpx.HTTPStatusError):
        await service.get_users_by_telegram_id(123456789)


async def test_get_users_by_telegram_id_filter_fallback_raises_on_server_error(service, panel):
    panel.on("GET", "/users/stream", _resp(404))
    panel.on("GET", "/users", _resp(500, {"message": "boom"}))

    with pytest.raises(httpx.HTTPStatusError):
        await service.get_users_by_telegram_id(123456789)


@pytest.mark.parametrize("telegram_id", [0, None])
async def test_get_users_by_telegram_id_falsy_returns_empty(service, panel, telegram_id):
    assert await service.get_users_by_telegram_id(telegram_id) == []
    assert panel.calls == []


async def test_get_users_by_telegram_id_never_uses_removed_or_ignored_endpoints(service, panel):
    panel.on("GET", "/users/stream", _resp(404))
    panel.on("GET", "/users", _resp(200, {"response": {"users": [], "total": 0}}))

    assert await service.get_users_by_telegram_id(123456789) == []

    for _, path, params, _ in panel.calls:
        assert not path.startswith("/users/by-telegram-id")
        assert "telegramId" not in (params or {}) or path == "/users/stream"


async def test_get_users_by_email_is_exact_and_case_insensitive(service, panel):
    panel.on(
        "GET",
        "/users",
        _resp(
            200,
            {
                "response": {
                    "users": [
                        _user(1, email="xjohn@example.com"),
                        _user(2, email="John@Example.com"),
                        _user(3, email=None),
                    ],
                    "total": 3,
                }
            },
        ),
    )

    users = await service.get_users_by_email("john@example.com")
    assert [u.id for u in users] == [2]


# --------------------------------------------------------------------------- is_owned_by


@pytest.mark.parametrize(
    ("panel_kwargs", "expected"),
    [
        ({"telegram_id": 123456789}, True),
        ({"telegram_id": 123456789, "username": "someone_else"}, True),
        ({"telegram_id": 987654321, "username": "rs_123456789"}, False),
        ({"telegram_id": None, "username": "rs_123456789"}, True),
        ({"telegram_id": None, "username": "rs_987654321"}, False),
        ({"telegram_id": None, "username": "manual", "email": "john@example.com"}, False),
    ],
)
def test_is_owned_by_telegram_user(service, sample_user_dto, panel_kwargs, expected):
    remna_user = service._parse_user_response(_user(10, **panel_kwargs))
    assert RemnawaveImpl.is_owned_by(remna_user, sample_user_dto) is expected


@pytest.mark.parametrize(
    ("panel_kwargs", "expected"),
    [
        ({"telegram_id": 123456789, "username": "rs_web_1"}, False),
        ({"telegram_id": None, "username": "rs_web_1"}, True),
        ({"telegram_id": None, "username": "other", "email": "JOHN@example.com"}, True),
        ({"telegram_id": None, "username": "rs_web_2"}, False),
        ({"telegram_id": None, "username": "other", "email": "other@example.com"}, False),
    ],
)
def test_is_owned_by_user_without_telegram(service, sample_user_dto, panel_kwargs, expected):
    sample_user_dto.telegram_id = None
    assert sample_user_dto.remna_name == "rs_web_1"
    remna_user = service._parse_user_response(_user(10, **panel_kwargs))
    assert RemnawaveImpl.is_owned_by(remna_user, sample_user_dto) is expected


@pytest.mark.parametrize(
    ("telegram_id", "panel_kwargs", "stored_remna_id", "expected"),
    [
        # Telegram-less bot user: imported panel user identified by the stored id.
        (None, {"telegram_id": None, "username": "imported"}, 10, True),
        (None, {"telegram_id": None, "username": "imported"}, 11, False),
        (None, {"telegram_id": None, "username": "imported"}, None, False),
        (None, {"telegram_id": None, "username": "imported", "email": "x@example.com"}, 0, False),
        # Never a Telegram-bound panel user, even with a matching stored id.
        (None, {"telegram_id": 555, "username": "rs_web_1"}, 10, False),
        # Telegram users keep strict rules: the stored id grants nothing.
        (123456789, {"telegram_id": None, "username": "imported"}, 10, False),
        (123456789, {"telegram_id": 555}, 10, False),
    ],
)
def test_is_owned_by_stored_remna_id(
    service, sample_user_dto, telegram_id, panel_kwargs, stored_remna_id, expected
):
    sample_user_dto.telegram_id = telegram_id
    remna_user = service._parse_user_response(_user(10, **panel_kwargs))
    assert RemnawaveImpl.is_owned_by(remna_user, sample_user_dto, stored_remna_id) is expected


# --------------------------------------------------------------------------- resolve_user


async def test_resolve_user_returns_owned_stored_id(service, panel, sample_user_dto):
    panel.on("GET", "/users/55", _resp(200, _one(_user(55, telegram_id=123456789))))

    resolved = await service.resolve_user(sample_user_dto, 55)

    assert resolved.id == 55
    assert panel.paths() == ["/users/55"]


async def test_resolve_user_ignores_foreign_stored_id(service, panel, sample_user_dto):
    panel.on("GET", "/users/149", _resp(200, _one(_user(149, telegram_id=111))))
    panel.on("GET", "/users/stream", _resp(200, _stream([_user(77, telegram_id=123456789)])))

    resolved = await service.resolve_user(sample_user_dto, 149)

    assert resolved.id == 77


async def test_resolve_user_returns_none_when_nothing_owned(service, panel, sample_user_dto):
    panel.on("GET", "/users/149", _resp(200, _one(_user(149, telegram_id=111))))
    panel.on("GET", "/users/stream", _resp(200, _stream([_user(5, telegram_id=1123456789)])))
    panel.on(
        "GET",
        f"/users/by-username/{sample_user_dto.remna_name}",
        _resp(404, {"message": "User not found", "errorCode": "A025"}),
    )

    assert await service.resolve_user(sample_user_dto, 149) is None


async def test_resolve_user_prefers_username_match(service, panel, sample_user_dto):
    panel.on(
        "GET",
        "/users/stream",
        _resp(
            200,
            _stream(
                [
                    _user(1, telegram_id=123456789, username="imported"),
                    _user(2, telegram_id=123456789, username=sample_user_dto.remna_name),
                ]
            ),
        ),
    )

    resolved = await service.resolve_user(sample_user_dto)
    assert resolved.id == 2


async def test_resolve_user_without_telegram_accepts_stored_imported_user(
    service, panel, sample_user_dto
):
    sample_user_dto.telegram_id = None
    panel.on("GET", "/users/149", _resp(200, _one(_user(149, username="imported"))))

    resolved = await service.resolve_user(sample_user_dto, 149)

    assert resolved.id == 149
    assert panel.paths() == ["/users/149"]


@pytest.mark.parametrize(
    "panel_kwargs",
    [{"telegram_id": 111, "username": "imported"}],
)
async def test_resolve_user_without_telegram_rejects_telegram_bound_stored_user(
    service, panel, sample_user_dto, panel_kwargs
):
    sample_user_dto.telegram_id = None
    panel.on("GET", "/users/149", _resp(200, _one(_user(149, **panel_kwargs))))
    panel.on(
        "GET",
        f"/users/by-username/{sample_user_dto.remna_name}",
        _resp(404, {"message": "User not found", "errorCode": "A025"}),
    )

    assert await service.resolve_user(sample_user_dto, 149) is None


async def test_resolve_user_without_telegram_rejects_other_unmatched_user(
    service, panel, sample_user_dto
):
    sample_user_dto.telegram_id = None
    # Found by username lookup, but neither username/email nor the stored id (149) match.
    panel.on("GET", "/users/149", _resp(404, {"message": "User not found", "errorCode": "A025"}))
    panel.on(
        "GET",
        f"/users/by-username/{sample_user_dto.remna_name}",
        _resp(200, _one(_user(150, username="someone", email="other@example.com"))),
    )

    assert await service.resolve_user(sample_user_dto, 149) is None


async def test_resolve_user_rejects_foreign_username_match(service, panel, sample_user_dto):
    panel.on("GET", "/users/stream", _resp(200, _stream([])))
    panel.on(
        "GET",
        f"/users/by-username/{sample_user_dto.remna_name}",
        _resp(200, _one(_user(9, telegram_id=111, username=sample_user_dto.remna_name))),
    )

    assert await service.resolve_user(sample_user_dto) is None


# --------------------------------------------------------------------------- create_user


async def test_create_user_posts_when_nothing_owned(
    service, panel, sample_user_dto, sample_plan_dto
):
    panel.on("GET", "/users/stream", _resp(200, _stream([])))

    def create(_params, payload):
        assert payload["username"] == sample_user_dto.remna_name
        assert payload["telegramId"] == sample_user_dto.telegram_id
        return _resp(201, _one(_user(555, telegram_id=123456789)), "POST", "/users")

    panel.on("POST", "/users", create)

    created = await service.create_user(sample_user_dto, sample_plan_dto)

    assert created.id == 555
    assert panel.paths("POST") == ["/users"]


async def test_create_user_reuses_owned_existing_user(
    service, panel, sample_user_dto, sample_plan_dto
):
    panel.on("GET", "/users/stream", _resp(200, _stream([_user(88, telegram_id=123456789)])))
    panel.on("PATCH", "/users", _resp(200, _one(_user(88, telegram_id=123456789)), "PATCH"))
    panel.on(
        "POST",
        "/users/88/actions/reset-traffic",
        _resp(200, _one(_user(88, telegram_id=123456789)), "POST"),
    )

    result = await service.create_user(sample_user_dto, sample_plan_dto)

    assert result.id == 88
    assert "/users" not in panel.paths("POST")
    assert panel.calls[-2][3]["id"] == 88


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, {"message": "User username already exists", "errorCode": "A019"}),
        (409, {"message": "conflict"}),
    ],
)
async def test_create_user_conflict(service, panel, sample_user_dto, sample_plan_dto, status, body):
    panel.on("GET", "/users/stream", _resp(200, _stream([])))
    panel.on("POST", "/users", _resp(status, body, "POST", "/users"))

    with pytest.raises(ConflictError):
        await service.create_user(sample_user_dto, sample_plan_dto)


async def test_create_user_other_400_is_not_conflict(
    service, panel, sample_user_dto, sample_plan_dto
):
    panel.on("GET", "/users/stream", _resp(200, _stream([])))
    panel.on("POST", "/users", _resp(400, {"message": "bad", "errorCode": "E000"}, "POST"))

    with pytest.raises(httpx.HTTPStatusError):
        await service.create_user(sample_user_dto, sample_plan_dto)


# --------------------------------------------------------------------------- update_user


async def test_update_user_owned_id_payload(service, panel, sample_user_dto, sample_plan_dto):
    panel.on("GET", "/users/555", _resp(200, _one(_user(555, telegram_id=123456789))))
    panel.on("PATCH", "/users", _resp(200, _one(_user(555, telegram_id=123456789)), "PATCH"))

    result = await service.update_user(sample_user_dto, id=555, plan=sample_plan_dto)

    assert result.id == 555
    patch_payload = [c[3] for c in panel.calls if c[0] == "PATCH"][0]
    assert patch_payload["id"] == 555
    assert "username" not in patch_payload
    assert patch_payload["telegramId"] == 123456789
    assert "/users/555/actions/reset-traffic" not in panel.paths("POST")


async def test_update_user_foreign_stored_id_patches_owned_user(
    service, panel, sample_user_dto, sample_plan_dto
):
    panel.on("GET", "/users/149", _resp(200, _one(_user(149, telegram_id=111))))
    panel.on("GET", "/users/stream", _resp(200, _stream([_user(300, telegram_id=123456789)])))
    panel.on("PATCH", "/users", _resp(200, _one(_user(300, telegram_id=123456789)), "PATCH"))
    panel.on(
        "POST",
        "/users/300/actions/reset-traffic",
        _resp(200, _one(_user(300, telegram_id=123456789)), "POST"),
    )

    result = await service.update_user(
        sample_user_dto, id=149, plan=sample_plan_dto, reset_traffic=True
    )

    assert result.id == 300
    patch_payloads = [c[3] for c in panel.calls if c[0] == "PATCH"]
    assert [p["id"] for p in patch_payloads] == [300]
    assert all("username" not in p for p in patch_payloads)
    assert panel.paths("POST") == ["/users/300/actions/reset-traffic"]
    assert not any("149" in p for p in panel.paths("POST") + panel.paths("DELETE"))


async def test_update_user_foreign_stored_id_creates_when_none_owned(
    service, panel, sample_user_dto, sample_plan_dto
):
    panel.on("GET", "/users/149", _resp(200, _one(_user(149, telegram_id=111))))
    panel.on("GET", "/users/stream", _resp(200, _stream([])))
    panel.on(
        "POST", "/users", _resp(201, _one(_user(901, telegram_id=123456789)), "POST", "/users")
    )

    result = await service.update_user(sample_user_dto, id=149, plan=sample_plan_dto)

    assert result.id == 901
    assert panel.paths("PATCH") == []
    assert panel.paths("POST") == ["/users"]


async def test_update_user_raises_binding_error_for_foreign_response(
    service, panel, sample_user_dto, sample_plan_dto
):
    panel.on("GET", "/users/555", _resp(200, _one(_user(555, telegram_id=123456789))))
    panel.on("PATCH", "/users", _resp(200, _one(_user(555, telegram_id=111)), "PATCH"))

    with pytest.raises(RemnaUserBindingError):
        await service.update_user(sample_user_dto, id=555, plan=sample_plan_dto)


async def test_update_user_patch_404_raises_not_found(
    service, panel, sample_user_dto, sample_plan_dto
):
    panel.on("GET", "/users/555", _resp(200, _one(_user(555, telegram_id=123456789))))
    panel.on("PATCH", "/users", _resp(404, {"message": "User not found", "errorCode": "A025"}))

    with pytest.raises(NotFoundError):
        await service.update_user(sample_user_dto, id=555, plan=sample_plan_dto)


# --------------------------------------------------------------------------- get_user_by_id


async def test_get_user_by_id_success(service, panel):
    panel.on("GET", "/users/100", _resp(200, _one(_user(100, telegram_id=1))))

    user = await service.get_user_by_id(100)

    assert user is not None and user.id == 100
    assert panel.paths() == ["/users/100"]


async def test_get_user_by_id_404_returns_none(service, panel):
    panel.on("GET", "/users/999", _resp(404, {"message": "User not found", "errorCode": "A025"}))
    assert await service.get_user_by_id(999) is None


async def test_get_user_by_id_500_raises(service, panel):
    panel.on("GET", "/users/999", _resp(500, {"message": "boom", "errorCode": "A001"}))

    with pytest.raises(httpx.HTTPStatusError):
        await service.get_user_by_id(999)


@pytest.mark.parametrize("bad_id", [0, -1])
async def test_get_user_by_id_invalid_id_skips_http(service, panel, bad_id):
    assert await service.get_user_by_id(bad_id) is None
    assert panel.calls == []


# --------------------------------------------------------------------------- actions


async def test_enable_and_disable_user(service, panel):
    panel.on("POST", "/users/100/actions/enable", _resp(200, _one(_user(100)), "POST"))
    panel.on("POST", "/users/100/actions/disable", _resp(200, _one(_user(100)), "POST"))

    await service.enable_user(id=100)
    await service.disable_user(id=100)

    assert panel.paths("POST") == ["/users/100/actions/enable", "/users/100/actions/disable"]


@pytest.mark.parametrize("bad_id", [0, -5])
@pytest.mark.parametrize("action", ["enable_user", "disable_user"])
async def test_enable_disable_invalid_id_raises_without_http(service, panel, action, bad_id):
    with pytest.raises(NotFoundError):
        await getattr(service, action)(id=bad_id)
    assert panel.calls == []


async def test_disable_user_404_raises_not_found(service, panel):
    with pytest.raises(NotFoundError):
        await service.disable_user(id=100)


async def test_delete_user(service, panel):
    panel.on("DELETE", "/users/100", _resp(200, {"response": {"isDeleted": True}}, "DELETE"))

    assert await service.delete_user(id=100) is True
    assert panel.paths("DELETE") == ["/users/100"]


async def test_hwid_and_connection_payloads(service, panel):
    devices = {"response": {"total": 0, "devices": []}}
    panel.on("POST", "/hwid/devices/delete", _resp(200, devices, "POST"))
    panel.on("POST", "/hwid/devices/delete-all", _resp(200, devices, "POST"))
    panel.on("POST", "/connections/drop", _resp(200, {"response": {}}, "POST"))

    assert await service.delete_device(12, "hw-1") == 0
    await service.delete_all_devices(12)
    await service.drop_connections(12)

    bodies = {path: body for method, path, _, body in panel.calls if method == "POST"}
    assert bodies["/hwid/devices/delete"] == {"userId": 12, "hwid": "hw-1"}
    assert bodies["/hwid/devices/delete-all"] == {"userId": 12}
    assert bodies["/connections/drop"] == {
        "dropBy": {"by": "userIds", "userIds": [12]},
        "targetNodes": {"target": "allNodes"},
    }
