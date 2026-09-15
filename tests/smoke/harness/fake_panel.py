# ruff: noqa: PLC0415
"""Stateful in-memory Remnawave panel.

The bot's real `RemnawaveImpl` talks to the panel over raw httpx calls; those go through an
`httpx.MockTransport` routed here, so request building/response parsing in `RemnawaveImpl`
is exercised for real. The typed SDK controllers used directly by dashboard getters
(system/hosts/nodes/inbounds/squads) are replaced with async stubs returning validated remnapy
models (see `install_sdk_stubs`).
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock

import httpx

INTERNAL_SQUAD_UUID = "11111111-1111-1111-1111-111111111111"
EXTERNAL_SQUAD_UUID = "22222222-2222-2222-2222-222222222222"
NODE_UUID = "33333333-3333-3333-3333-333333333333"
PROFILE_UUID = "44444444-4444-4444-4444-444444444444"
INBOUND_UUID = "55555555-5555-5555-5555-555555555555"
NOW = "2026-01-01T00:00:00Z"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakePanel:
    def __init__(self) -> None:
        self.users: dict[int, dict[str, Any]] = {}
        self.requests: list[tuple[str, str, Any]] = []
        self.unhandled: list[tuple[str, str]] = []
        self._next_id = 500

    def reset(self) -> None:
        self.users.clear()
        self.requests.clear()
        self.unhandled.clear()
        self._next_id = 500

    # ------------------------------------------------------------------ data helpers
    def add_user(
        self,
        *,
        username: str,
        telegram_id: Optional[int] = None,
        email: Optional[str] = None,
        expire_in_days: int = 30,
        status: str = "ACTIVE",
    ) -> dict[str, Any]:
        self._next_id += 1
        remna_id = self._next_id
        user = {
            "id": remna_id,
            "uuid": str(uuid.UUID(int=remna_id)),
            "shortUuid": f"short{remna_id}",
            "username": username,
            "status": status,
            "trafficLimitBytes": 100 * 1024**3,
            "trafficLimitStrategy": "NO_RESET",
            "expireAt": _iso(datetime.now(timezone.utc) + timedelta(days=expire_in_days)),
            "telegramId": telegram_id,
            "email": email,
            "hwidDeviceLimit": 3,
            "subscriptionUrl": f"https://sub.example.com/{remna_id}",
            "activeInternalSquads": [{"uuid": INTERNAL_SQUAD_UUID, "name": "Default"}],
            "externalSquadUuid": None,
            "usedTrafficBytes": 1024**3,
            "lifetimeUsedTrafficBytes": 2 * 1024**3,
            "createdAt": NOW,
            "updatedAt": NOW,
        }
        self.users[remna_id] = user
        return user

    def _apply_payload(self, user: dict[str, Any], payload: dict[str, Any]) -> None:
        for key, value in payload.items():
            if key in {"id", "uuid"}:
                continue
            if key == "activeInternalSquads":
                value = [{"uuid": str(s), "name": "Default"} for s in (value or [])]
            user[key] = value
        user["updatedAt"] = NOW

    # ------------------------------------------------------------------ transport
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:  # noqa: C901
        method = request.method
        path = request.url.path
        if path.startswith("/api"):
            path = path[len("/api") :]
        body: Any = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = request.content
        self.requests.append((method, path, body))
        params = request.url.params

        def ok(data: Any, status: int = 200) -> httpx.Response:
            return httpx.Response(status, json={"response": data}, request=request)

        def not_found() -> httpx.Response:
            return httpx.Response(
                404, json={"message": "Not found", "errorCode": "A063"}, request=request
            )

        if method == "GET" and path == "/users/stream":
            telegram_id = params.get("telegramId")
            users = [
                u
                for u in self.users.values()
                if telegram_id is None or str(u.get("telegramId")) == telegram_id
            ]
            return ok({"users": users, "hasMore": False, "nextCursor": None})

        if method == "GET" and path == "/users":
            users = list(self.users.values())
            raw_filters = params.get("filters")
            if raw_filters:
                for flt in json.loads(raw_filters):
                    users = [u for u in users if str(u.get(flt["id"])) == str(flt["value"])]
            start = int(params.get("start", 0))
            size = int(params.get("size", 100))
            return ok({"users": users[start : start + size], "total": len(users)})

        if method == "POST" and path == "/users":
            payload = body or {}
            for existing in self.users.values():
                if existing["username"] == payload.get("username"):
                    return httpx.Response(
                        409, json={"message": "exists", "errorCode": "A019"}, request=request
                    )
            user = self.add_user(
                username=payload.get("username", f"user{self._next_id + 1}"),
                telegram_id=payload.get("telegramId"),
                email=payload.get("email"),
            )
            self._apply_payload(user, payload)
            return ok(user, 201)

        if method == "PATCH" and path == "/users":
            payload = body or {}
            user = self.users.get(int(payload.get("id", 0) or 0))
            if user is None:
                return not_found()
            self._apply_payload(user, payload)
            return ok(user)

        if m := re.fullmatch(r"/users/by-username/(.+)", path):
            if method == "GET":
                for user in self.users.values():
                    if user["username"] == m.group(1):
                        return ok(user)
                return not_found()

        if m := re.fullmatch(r"/users/(\d+)/actions/([a-z-]+)", path):
            user = self.users.get(int(m.group(1)))
            if user is None:
                return not_found()
            action = m.group(2)
            if action == "enable":
                user["status"] = "ACTIVE"
            elif action == "disable":
                user["status"] = "DISABLED"
            elif action == "reset-traffic":
                user["usedTrafficBytes"] = 0
            elif action == "revoke":
                user["shortUuid"] = f"revoked{user['id']}"
            return ok(user)

        if m := re.fullmatch(r"/users/(\d+)", path):
            remna_id = int(m.group(1))
            if method == "GET":
                return ok(self.users[remna_id]) if remna_id in self.users else not_found()
            if method == "DELETE":
                if self.users.pop(remna_id, None) is None:
                    return not_found()
                return ok({"isDeleted": True})

        if m := re.fullmatch(r"/hwid/devices/(\d+)", path):
            if int(m.group(1)) not in self.users:
                return not_found()
            return ok({"total": 1, "devices": [self._device(int(m.group(1)))]})

        if method == "POST" and path in {"/hwid/devices/delete", "/hwid/devices/delete-all"}:
            return ok({"total": 0, "devices": []})

        if method == "POST" and path == "/connections/drop":
            return ok({"eventSent": True})

        self.unhandled.append((method, path))
        return not_found()

    @staticmethod
    def _device(remna_id: int) -> dict[str, Any]:
        return {
            "hwid": f"hwid-{remna_id}",
            "userId": remna_id,
            "platform": "android",
            "osVersion": "14",
            "deviceModel": "Pixel",
            "userAgent": "Happ/1.0",
            "createdAt": NOW,
            "updatedAt": NOW,
        }


def install_sdk_stubs(sdk: Any, panel: FakePanel) -> None:
    """Replace typed SDK controllers used directly by the bot with validated model stubs."""
    from remnapy.models import (
        GetAllHostsResponseDto,
        GetAllInboundsResponseDto,
        GetAllInternalSquadsResponseDto,
        GetAllNodesResponseDto,
        GetExternalSquadByUuidResponseDto,
        GetExternalSquadsResponseDto,
        GetOneNodeResponseDto,
        GetStatsResponseDto,
    )
    from remnapy.models.system import GetMetadataResponseDto

    node = {
        "uuid": NODE_UUID,
        "name": "Smoke-Node",
        "address": "node.example.com",
        "port": 2222,
        "isConnected": True,
        "isDisabled": False,
        "isConnecting": False,
        "xrayUptime": 3600,
        "isTrafficTrackingActive": True,
        "trafficLimitBytes": 10 * 1024**3,
        "trafficUsedBytes": 1024**3,
        "usersOnline": 3,
        "viewPosition": 1,
        "countryCode": "NL",
        "consumptionMultiplier": 1.0,
        "createdAt": NOW,
        "updatedAt": NOW,
        "configProfile": {"activeConfigProfileUuid": PROFILE_UUID, "activeInbounds": []},
    }
    host = {
        "uuid": str(uuid.uuid4()),
        "viewPosition": 1,
        "remark": "Smoke host",
        "address": "host.example.com",
        "port": 443,
        "path": None,
        "sni": None,
        "host": None,
        "alpn": None,
        "fingerprint": None,
        "xhttpExtraParams": None,
        "muxParams": None,
        "sockoptParams": None,
        "inbound": {"configProfileUuid": PROFILE_UUID, "configProfileInboundUuid": INBOUND_UUID},
        "serverDescription": None,
        "vlessRouteId": None,
        "shuffleHost": False,
        "mihomoX25519": False,
        "nodes": [NODE_UUID],
        "xrayJsonTemplateUuid": None,
    }
    external_squad = {
        "uuid": EXTERNAL_SQUAD_UUID,
        "viewPosition": 1,
        "name": "External",
        "info": {"membersCount": 1},
        "templates": [],
        "createdAt": NOW,
        "updatedAt": NOW,
    }

    sdk.system = SimpleNamespace(
        get_metadata=AsyncMock(
            side_effect=lambda: GetMetadataResponseDto.model_validate(_metadata())
        ),
        get_stats=AsyncMock(
            side_effect=lambda: GetStatsResponseDto.model_validate(
                {
                    "cpu": {"cores": 4, "physicalCores": 2},
                    "memory": {"total": 8 * 1024**3, "free": 4 * 1024**3, "used": 4 * 1024**3},
                    "uptime": 7200,
                    "timestamp": 0,
                    "users": {
                        "statusCounts": {"ACTIVE": len(panel.users), "DISABLED": 0},
                        "totalUsers": len(panel.users),
                    },
                    "onlineStats": {
                        "lastDay": 1,
                        "lastWeek": 2,
                        "neverOnline": 0,
                        "onlineNow": 1,
                    },
                    "nodes": {"totalOnline": 1, "totalBytesLifetime": "1024"},
                }
            )
        ),
    )
    sdk.hosts = SimpleNamespace(
        get_all_hosts=AsyncMock(side_effect=lambda: GetAllHostsResponseDto.model_validate([host]))
    )
    sdk.nodes = SimpleNamespace(
        get_all_nodes=AsyncMock(side_effect=lambda: GetAllNodesResponseDto.model_validate([node])),
        get_one_node=AsyncMock(
            side_effect=lambda *a, **k: GetOneNodeResponseDto.model_validate(node)
        ),
    )
    inbounds = {
        "total": 1,
        "inbounds": [
            {
                "uuid": INBOUND_UUID,
                "profileUuid": PROFILE_UUID,
                "tag": "VLESS",
                "type": "vless",
                "network": "tcp",
                "security": "reality",
                "port": 443,
            }
        ],
    }
    sdk.inbounds = SimpleNamespace(
        get_all_inbounds=AsyncMock(
            side_effect=lambda: GetAllInboundsResponseDto.model_validate(inbounds)
        )
    )
    sdk.config_profiles = SimpleNamespace(get_all_inbounds=sdk.inbounds.get_all_inbounds)
    sdk.internal_squads = SimpleNamespace(
        get_internal_squads=AsyncMock(
            side_effect=lambda: GetAllInternalSquadsResponseDto.model_validate(
                {
                    "total": 1,
                    "internalSquads": [
                        {
                            "uuid": INTERNAL_SQUAD_UUID,
                            "viewPosition": 1,
                            "name": "Default",
                            "info": {"membersCount": 1, "inboundsCount": 1},
                            "inbounds": [],
                            "createdAt": NOW,
                            "updatedAt": NOW,
                        }
                    ],
                }
            )
        )
    )
    sdk.external_squads = SimpleNamespace(
        get_external_squads=AsyncMock(
            side_effect=lambda: GetExternalSquadsResponseDto.model_validate(
                {"total": 1, "externalSquads": [external_squad]}
            )
        ),
        get_external_squad_by_uuid=AsyncMock(
            side_effect=lambda *a, **k: GetExternalSquadByUuidResponseDto.model_validate(
                external_squad
            )
        ),
    )


def _metadata() -> dict[str, Any]:
    # Filled in lazily from the model definition (see harness/app.py for the version used).
    from tests.smoke.harness import PANEL_VERSION

    return {
        "version": PANEL_VERSION,
        "build": {"time": NOW, "number": "1"},
        "git": {
            "backend": {"commitSha": "abc", "branch": "main", "commitUrl": "https://x"},
            "frontend": {"commitSha": "abc", "commitUrl": "https://x"},
        },
    }
