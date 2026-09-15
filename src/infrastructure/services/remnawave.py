import asyncio
import json
from dataclasses import fields, is_dataclass
from datetime import timedelta
from typing import Any, Optional, Union
from uuid import UUID

from loguru import logger
from packaging.version import Version
from remnapy import RemnawaveSDK
from remnapy.exceptions import (
    ApiErrorResponse,
    AuthenticationError,
    ConflictError,
    NotFoundError,
)
from remnapy.models import GetMetadataResponseDto, UserResponseDto
from remnapy.models.hwid import HwidDeviceDto

from src.application.common import Remnawave
from src.application.common.remnawave import T
from src.application.dto import (
    PlanSnapshotDto,
    RemnaSubscriptionDto,
    SquadInfoDto,
    SubscriptionDto,
    UserDto,
)
from src.core.constants import REMNAWAVE_MIN_VERSION
from src.core.enums import SubscriptionStatus
from src.core.exceptions import RemnaUserBindingError
from src.core.utils.converters import days_to_datetime, gb_to_bytes
from src.core.utils.time import datetime_now

# GET /users `filters` use LIKE/ILIKE under the hood (backend users.repository.ts
# applyUsersFilters: `CAST(telegram_id AS TEXT) LIKE %value%`, email `ILIKE %value%`), so they may
# return users whose value merely contains the searched one. Results are always re-checked for
# exact equality. GET /users/stream (available since 3.0.0) filters telegramId/email with `=`.
# Panel limits: size <= 1000 for both endpoints.
_FILTER_PAGE_SIZE = 100
_STREAM_PAGE_SIZE = 250
# ERRORS.USER_USERNAME_ALREADY_EXISTS in libs/contract/constants/errors/errors.ts (HTTP 400).
_USERNAME_ALREADY_EXISTS_CODE = "A019"


def _not_found(id: Union[int, str]) -> NotFoundError:
    return NotFoundError(
        status_code=404,
        error=ApiErrorResponse(message=f"User {id} not found", code="USER_NOT_FOUND"),
    )


class RemnawaveImpl(Remnawave):
    def __init__(self, sdk: RemnawaveSDK) -> None:
        self.sdk = sdk
        self._panel_version: Optional[Version] = None

    @property
    def _client(self) -> Any:
        client = self.sdk._client
        if client is None:
            raise RuntimeError("Remnawave HTTP client is not initialized")
        return client

    async def try_connection(self) -> Version:
        for attempt in range(1, 4):
            try:
                metadata = await self.sdk.system.get_metadata()
                break
            except AuthenticationError as e:
                logger.error(f"Authentication failed when connecting to Remnawave panel: '{e}'")
                raise
            except Exception as e:
                if attempt < 3:
                    logger.warning(
                        f"Failed to connect to Remnawave panel (attempt {attempt}/3): '{e}', "
                        f"retrying in 5s..."
                    )
                    await asyncio.sleep(5)
                else:
                    logger.error(f"Failed to connect to Remnawave panel after 3 attempts: '{e}'")
                    raise

        if not isinstance(metadata, GetMetadataResponseDto):
            logger.error(f"Invalid response from Remnawave panel: '{metadata}'")
            raise ValueError(f"Invalid response from Remnawave panel: {metadata}")

        panel_version = Version(metadata.version)
        if panel_version < REMNAWAVE_MIN_VERSION:
            logger.error(
                f"Remnawave panel version '{panel_version}' is not compatible. "
                f"Minimum required version: '{REMNAWAVE_MIN_VERSION}'"
            )
            raise ValueError(
                f"Remnawave panel version '{panel_version}' is not compatible. "
                f"Minimum required version: '{REMNAWAVE_MIN_VERSION}'"
            )

        self._panel_version = panel_version
        logger.info(f"Successfully connected to Remnawave panel (version: {panel_version})")
        return panel_version

    @staticmethod
    def _normalize_user_dict(data: dict[str, Any]) -> dict[str, Any]:
        """Normalizes a v3 user dictionary so remnapy's UserResponseDto validation succeeds."""
        d = dict(data)
        remna_id = d.get("id", 0)
        vless = d.get("vlessUuid") or d.get("vless_uuid")
        fallback_uuid = vless or f"00000000-0000-0000-0000-{int(remna_id):012d}"

        d.setdefault("uuid", fallback_uuid)
        d.setdefault("vlessUuid", d["uuid"])
        d.setdefault("shortUuid", str(remna_id))
        d.setdefault("trojanPassword", "")
        d.setdefault("ssPassword", "")
        d.setdefault("subscriptionUrl", "")
        d.setdefault("activeInternalSquads", [])
        d.setdefault("lastTriggeredThreshold", 0)
        d.setdefault("trafficLimitBytes", 0)
        d.setdefault("trafficLimitStrategy", "NO_RESET")
        d.setdefault("createdAt", "2026-01-01T00:00:00Z")
        d.setdefault("updatedAt", "2026-01-01T00:00:00Z")

        if not d.get("expireAt") and not d.get("expire_at"):
            d["expireAt"] = "2099-01-01T00:00:00Z"

        if "userTraffic" not in d and "user_traffic" not in d:
            d["userTraffic"] = {
                "usedTrafficBytes": int(d.get("usedTrafficBytes", 0)),
                "lifetimeUsedTrafficBytes": int(d.get("lifetimeUsedTrafficBytes", 0)),
                "onlineAt": d.get("onlineAt"),
                "firstConnectedAt": d.get("firstConnectedAt"),
                "lastConnectedNodeUuid": d.get("lastConnectedNodeUuid"),
            }

        return d

    def _parse_user_response(self, data: dict[str, Any]) -> UserResponseDto:
        raw_user = data.get("response", data) if isinstance(data, dict) else data
        normalized = self._normalize_user_dict(raw_user)
        return UserResponseDto.model_validate(normalized)

    def _parse_users_list(self, data: dict[str, Any]) -> list[UserResponseDto]:
        raw = data.get("response", data) if isinstance(data, dict) else data
        if isinstance(raw, dict):
            users_raw = raw.get("users", [])
        elif isinstance(raw, list):
            users_raw = raw
        else:
            users_raw = []
        return [self._parse_user_response(u) for u in users_raw]

    @staticmethod
    def is_owned_by(
        remna_user: UserResponseDto,
        user: UserDto,
        stored_remna_id: Optional[int] = None,
    ) -> bool:
        if user.telegram_id:
            if remna_user.telegram_id is not None:
                return remna_user.telegram_id == user.telegram_id
            # Panel user without telegramId: only accept the username this bot generated for us.
            return remna_user.username == user.remna_name

        # Bot user without telegram_id (web/imported): never take a Telegram-bound panel user, and
        # require a positive identity match so another Telegram-less panel user is not taken.
        if remna_user.telegram_id is not None:
            return False
        if remna_user.username == user.remna_name:
            return True
        if user.email and remna_user.email:
            if remna_user.email.strip().lower() == user.email.strip().lower():
                return True
        # Imported panel users keep their own username: the id stored in this user's current
        # subscription (unique across current subscriptions by the DB guard) identifies them.
        return bool(stored_remna_id and stored_remna_id > 0 and remna_user.id == stored_remna_id)

    async def resolve_user(
        self,
        user: UserDto,
        remna_id: Optional[int] = None,
    ) -> Optional[UserResponseDto]:
        if remna_id and remna_id > 0:
            by_id = await self.get_user_by_id(remna_id)
            if by_id and self.is_owned_by(by_id, user, remna_id):
                return by_id
            if by_id:
                logger.warning(
                    f"Stored RemnaUser ID '{remna_id}' belongs to telegram_id "
                    f"'{by_id.telegram_id}' (username '{by_id.username}'), not to user "
                    f"{user.log}. Ignoring stored ID"
                )

        if user.telegram_id:
            candidates = await self.get_users_by_telegram_id(user.telegram_id)
            if candidates:
                if len(candidates) > 1:
                    logger.warning(
                        f"Found {len(candidates)} RemnaUsers with telegram_id "
                        f"'{user.telegram_id}': {[c.id for c in candidates]}"
                    )
                for candidate in candidates:
                    if candidate.username == user.remna_name:
                        return candidate
                return candidates[0]

        by_username = await self.get_user_by_username(user.remna_name)
        if by_username and self.is_owned_by(by_username, user):
            return by_username

        return None

    async def create_user(
        self,
        user: UserDto,
        plan: Optional[PlanSnapshotDto] = None,
        subscription: Optional[SubscriptionDto] = None,
    ) -> UserResponseDto:
        existing = await self.resolve_user(user)
        if existing:
            logger.info(
                f"RemnaUser '{existing.id}' already exists for user {user.log}. "
                "Updating instead of creating"
            )
            return await self._patch_user(user, existing.id, plan, subscription, reset_traffic=True)

        payload = self._build_v3_create_payload(user, plan, subscription)
        response = await self._client.post("/users", json=payload)
        if response.status_code == 409 or (
            response.status_code == 400
            and self._error_code(response) == _USERNAME_ALREADY_EXISTS_CODE
        ):
            logger.warning(f"RemnaUser '{user.remna_name}' already exists in panel (409 Conflict)")
            raise ConflictError(
                status_code=409,
                error=ApiErrorResponse(
                    message=f"User {user.remna_name} already exists",
                    code="USER_ALREADY_EXISTS",
                ),
            )
        if response.status_code not in {200, 201}:
            logger.error(
                f"Failed to create RemnaUser '{user.remna_name}': "
                f"status {response.status_code}, body: {response.text}"
            )
            response.raise_for_status()

        remna_user = self._parse_user_response(response.json())
        logger.info(
            f"RemnaUser '{remna_user.username}' created successfully. "
            f"ID: '{remna_user.id}', telegram_id: '{remna_user.telegram_id}'"
        )
        return remna_user

    async def update_user(
        self,
        user: UserDto,
        id: int,
        plan: Optional[PlanSnapshotDto] = None,
        subscription: Optional[SubscriptionDto] = None,
        reset_traffic: bool = False,
    ) -> UserResponseDto:
        """Update the panel user owned by `user`.

        `id` is only a hint: the target is re-resolved and verified to belong to `user`, so a
        wrong stored ID can never modify somebody else's panel user. If no panel user owned by
        `user` exists, a new one is created. Callers MUST persist the returned `.id`.
        """
        target = await self.resolve_user(user, id)

        if target is None:
            logger.warning(
                f"No RemnaUser owned by {user.log} found (stored ID '{id}'). Creating a new one"
            )
            return await self.create_user(user=user, plan=plan, subscription=subscription)

        if target.id != id:
            logger.warning(f"RemnaUser ID for {user.log} re-resolved: '{id}' -> '{target.id}'")

        return await self._patch_user(user, target.id, plan, subscription, reset_traffic)

    async def _patch_user(
        self,
        user: UserDto,
        id: int,
        plan: Optional[PlanSnapshotDto],
        subscription: Optional[SubscriptionDto],
        reset_traffic: bool,
    ) -> UserResponseDto:
        payload = self._build_v3_update_payload(user, id, plan, subscription)
        response = await self._client.patch("/users", json=payload)
        if response.status_code == 404:
            logger.warning(f"RemnaUser '{id}' for {user.log} not found (404)")
            raise _not_found(id)
        if response.status_code not in {200, 201}:
            logger.error(
                f"Failed to update RemnaUser '{id}': "
                f"status {response.status_code}, body: {response.text}"
            )
            response.raise_for_status()

        remna_user = self._parse_user_response(response.json())
        if remna_user.id != id or not self.is_owned_by(remna_user, user, id):
            # Should be impossible after resolve_user; fail loudly rather than silently rebinding.
            raise RemnaUserBindingError(
                f"Updated RemnaUser '{remna_user.id}' does not belong to user {user.log}"
            )

        logger.info(
            f"RemnaUser '{remna_user.username}' updated successfully. "
            f"ID: '{remna_user.id}', telegram_id: '{remna_user.telegram_id}'"
        )
        if reset_traffic:
            await self.reset_traffic(remna_user.id)
        return remna_user

    async def _post_action(self, id: int, action: str) -> None:
        if not id or id <= 0:
            raise _not_found(id)
        response = await self._client.post(f"/users/{id}/actions/{action}")
        if response.status_code == 404:
            logger.debug(f"RemnaUser '{id}' not found in panel")
            raise _not_found(id)
        if response.status_code not in {200, 201, 204}:
            response.raise_for_status()
        logger.info(f"RemnaUser '{id}' action '{action}' succeeded")

    async def enable_user(self, id: int) -> None:
        await self._post_action(id, "enable")

    async def disable_user(self, id: int) -> None:
        await self._post_action(id, "disable")

    async def delete_user(self, id: int) -> bool:
        if not id or id <= 0:
            return False
        response = await self._client.delete(f"/users/{id}")
        if response.status_code == 404:
            logger.debug(f"RemnaUser '{id}' not found in panel")
            return False
        if response.status_code in {200, 204}:
            logger.info(f"RemnaUser '{id}' deleted successfully")
            return True
        logger.warning(f"Failed to delete RemnaUser '{id}': status {response.status_code}")
        return False

    async def get_user_by_id(self, id: int) -> Optional[UserResponseDto]:
        if not id or id <= 0:
            return None

        response = await self._client.get(f"/users/{id}")
        if response.status_code == 200:
            logger.debug(f"Fetched RemnaUser '{id}' from panel")
            return self._parse_user_response(response.json())
        if response.status_code in {400, 404}:
            logger.debug(f"RemnaUser '{id}' not found in panel")
            return None

        # Do not turn transport/server errors into "not found": callers would recreate or unlink.
        logger.error(f"Failed to fetch RemnaUser '{id}': status {response.status_code}")
        response.raise_for_status()
        return None

    async def get_user_by_username(self, username: str) -> Optional[UserResponseDto]:
        response = await self._client.get(f"/users/by-username/{username}")
        if response.status_code == 200:
            return self._parse_user_response(response.json())
        if response.status_code in {400, 404}:
            return None
        response.raise_for_status()
        return None

    async def _get_users_by_filter(self, field: str, value: Any) -> list[UserResponseDto]:
        users: list[UserResponseDto] = []
        start = 0
        filters_param = json.dumps([{"id": field, "value": str(value)}])

        while True:
            response = await self._client.get(
                "/users",
                params={"filters": filters_param, "start": start, "size": _FILTER_PAGE_SIZE},
            )
            if response.status_code != 200:
                logger.error(
                    f"Failed to query RemnaUsers by '{field}': status {response.status_code}"
                )
                response.raise_for_status()
                raise RuntimeError(f"Unexpected status {response.status_code} from /users")

            batch = self._parse_users_list(response.json())
            users.extend(batch)
            if len(batch) < _FILTER_PAGE_SIZE:
                return users
            start += len(batch)

    @staticmethod
    def _error_code(response: Any) -> Optional[str]:
        try:
            body = response.json()
        except Exception:
            return None
        return body.get("errorCode") if isinstance(body, dict) else None

    async def _stream_users(self, params: dict[str, Any]) -> Optional[list[UserResponseDto]]:
        """Cursor-paginated GET /users/stream. Returns None if the endpoint is unavailable
        (404, or 403 for API tokens scoped to specific user endpoints)."""
        users: list[UserResponseDto] = []
        cursor: Optional[str] = None

        while True:
            query: dict[str, Any] = {**params, "size": _STREAM_PAGE_SIZE}
            if cursor is not None:
                query["cursor"] = cursor
            response = await self._client.get("/users/stream", params=query)
            if response.status_code in {403, 404}:
                logger.debug(f"GET /users/stream unavailable (status {response.status_code})")
                return None
            if response.status_code != 200:
                logger.error(f"Failed to stream RemnaUsers: status {response.status_code}")
                response.raise_for_status()
                raise RuntimeError(f"Unexpected status {response.status_code} from /users/stream")

            data = response.json()
            users.extend(self._parse_users_list(data))
            raw = data.get("response", {}) if isinstance(data, dict) else {}
            cursor = raw.get("nextCursor") if isinstance(raw, dict) else None
            if not (isinstance(raw, dict) and raw.get("hasMore")) or not cursor:
                return users

    async def get_users_by_telegram_id(self, telegram_id: int) -> list[UserResponseDto]:
        if not telegram_id:
            return []

        candidates = await self._stream_users({"telegramId": str(telegram_id)})
        if candidates is None:
            candidates = await self._get_users_by_filter("telegramId", telegram_id)
        matched = [u for u in candidates if u.telegram_id == telegram_id]
        logger.debug(
            f"RemnaUsers for telegram_id '{telegram_id}': {[u.id for u in matched]} "
            f"(filter returned {len(candidates)})"
        )
        return matched

    async def get_users_by_email(self, email: str) -> list[UserResponseDto]:
        if not email:
            return []

        candidates = await self._get_users_by_filter("email", email)
        wanted = email.strip().lower()
        return [u for u in candidates if u.email and u.email.strip().lower() == wanted]

    async def get_all_users(self, limit: int, offset: int) -> list[UserResponseDto]:
        response = await self._client.get("/users", params={"start": offset, "size": limit})
        if response.status_code != 200:
            response.raise_for_status()
        users = self._parse_users_list(response.json())
        logger.debug(f"Fetched {len(users)} RemnaUsers (limit={limit}, offset={offset})")
        return users

    @staticmethod
    def _as_remna_id(id: Union[int, UUID, str, None]) -> Optional[int]:
        if id is None:
            return None
        try:
            value = int(str(id))
        except ValueError:
            return None
        return value if value > 0 else None

    async def get_devices(self, id: Union[int, UUID, str]) -> list[HwidDeviceDto]:
        remna_id = self._as_remna_id(id)
        if remna_id is None:
            logger.warning(f"Skipping get_devices for invalid RemnaUser ID '{id}'")
            return []

        try:
            response = await self._client.get(f"/hwid/devices/{remna_id}")
            if response.status_code in {400, 404}:
                return []
            if response.status_code != 200:
                logger.warning(
                    f"Failed to fetch devices for RemnaUser '{id}': status {response.status_code}"
                )
                return []
            data = response.json().get("response", {})
            devices = [HwidDeviceDto.model_validate(d) for d in data.get("devices", [])]
            logger.debug(f"Fetched {len(devices)} devices for RemnaUser '{id}'")
            return devices
        except Exception as e:
            logger.warning(f"Error fetching devices for RemnaUser '{id}': {e}")
            return []

    async def delete_device(self, user_id: Union[int, UUID, str], hwid: str) -> Optional[int]:
        remna_id = self._as_remna_id(user_id)
        if remna_id is None:
            return None

        response = await self._client.post(
            "/hwid/devices/delete", json={"userId": remna_id, "hwid": hwid}
        )
        if response.status_code in {400, 404}:
            logger.debug(f"RemnaUser '{user_id}' not found in panel")
            return None
        if response.status_code in {200, 201}:
            total = int(response.json().get("response", {}).get("total", 0))
            logger.info(
                f"Deleted HWID device '{hwid}' for RemnaUser '{user_id}'. "
                f"Total devices now: {total}"
            )
            return total
        return None

    async def delete_all_devices(self, user_id: Union[int, UUID, str]) -> None:
        remna_id = self._as_remna_id(user_id)
        if remna_id is None:
            return

        response = await self._client.post("/hwid/devices/delete-all", json={"userId": remna_id})
        if response.status_code in {200, 201, 204}:
            logger.info(f"Deleted all HWID devices for RemnaUser '{user_id}'")
        else:
            logger.warning(
                f"Failed to delete all HWID devices for RemnaUser '{user_id}': "
                f"status {response.status_code}"
            )

    async def drop_connections(self, user_id: int) -> None:
        remna_id = self._as_remna_id(user_id)
        if remna_id is None:
            return

        try:
            await self._client.post(
                "/connections/drop",
                json={
                    "dropBy": {"by": "userIds", "userIds": [remna_id]},
                    "targetNodes": {"target": "allNodes"},
                },
            )
            logger.info(f"Dropped connections for RemnaUser '{remna_id}'")
        except Exception as e:
            logger.warning(f"Failed to drop connections for RemnaUser '{remna_id}': {e}")

    async def reset_traffic(self, id: int) -> Optional[UserResponseDto]:
        if not id or id <= 0:
            logger.warning(f"Skipping reset_traffic for invalid RemnaUser ID '{id}'")
            return None

        response = await self._client.post(f"/users/{id}/actions/reset-traffic")
        if response.status_code == 404:
            logger.debug(f"RemnaUser '{id}' not found in panel")
            return None
        if response.status_code in {200, 201}:
            remna_user = self._parse_user_response(response.json())
            logger.info(f"Traffic for RemnaUser '{remna_user.id}' reset successfully")
            return remna_user
        response.raise_for_status()
        return None

    async def revoke_subscription(self, id: int) -> None:
        if not id or id <= 0:
            raise _not_found(id)

        response = await self._client.post(f"/users/{id}/actions/revoke")
        if response.status_code == 404:
            logger.debug(f"RemnaUser '{id}' not found in panel")
            return
        if response.status_code not in {200, 201, 204}:
            response.raise_for_status()
        logger.info(f"Subscription for RemnaUser '{id}' revoked successfully")

    async def get_squads_available(self) -> bool:
        result = await self.sdk.internal_squads.get_internal_squads()
        return bool(result.internal_squads)

    async def get_internal_squads(self) -> list[SquadInfoDto]:
        result = await self.sdk.internal_squads.get_internal_squads()
        return [SquadInfoDto(uuid=s.uuid, name=s.name) for s in result.internal_squads]

    async def get_external_squads(self) -> list[SquadInfoDto]:
        result = await self.sdk.external_squads.get_external_squads()
        return [SquadInfoDto(uuid=s.uuid, name=s.name) for s in result.external_squads]

    def apply_sync(self, target: T, source: Union[SubscriptionDto, RemnaSubscriptionDto]) -> T:
        if not is_dataclass(target) or not is_dataclass(source):
            raise TypeError("Both target and source must be dataclasses")

        target_fields = {f.name for f in fields(target)}
        source_fields = {f.name for f in fields(source)}

        field_map = {"user_remna_id": "id"}

        for target_field, source_field in field_map.items():
            if target_field in target_fields and source_field in source_fields:
                old_value = getattr(target, target_field)
                new_value = getattr(source, source_field)

                if old_value != new_value:
                    logger.debug(
                        f"Field '{target_field}' changed from '{old_value}' to '{new_value}'"
                    )
                    setattr(target, target_field, new_value)

        common_fields = target_fields & source_fields

        for field_name in common_fields:
            old_value = getattr(target, field_name)
            new_value = getattr(source, field_name)

            if old_value != new_value:
                logger.debug(f"Field '{field_name}' changed from '{old_value}' to '{new_value}'")
                setattr(target, field_name, new_value)

        return target

    def _build_v3_create_payload(
        self,
        user: UserDto,
        plan: Optional[PlanSnapshotDto],
        subscription: Optional[SubscriptionDto],
    ) -> dict[str, Any]:
        traffic_limit_strategy = "NO_RESET"
        traffic_limit_bytes = 0
        device_limit = None
        tag = None
        active_squads: list[str] = []
        external_squad = None

        if subscription:
            expire_at_dt = subscription.expire_at
            traffic_limit_strategy = subscription.traffic_limit_strategy.value
            traffic_limit_bytes = gb_to_bytes(subscription.traffic_limit)
            device_limit = subscription.device_limit
            tag = subscription.tag
            active_squads = [str(s) for s in subscription.internal_squads]
            external_squad = (
                str(subscription.external_squad) if subscription.external_squad else None
            )
        elif plan:
            expire_at_dt = days_to_datetime(plan.duration)
            traffic_limit_strategy = plan.traffic_limit_strategy.value
            traffic_limit_bytes = gb_to_bytes(plan.traffic_limit)
            device_limit = plan.device_limit
            tag = plan.tag
            active_squads = [str(s) for s in plan.internal_squads]
            external_squad = str(plan.external_squad) if plan.external_squad else None
        else:
            expire_at_dt = datetime_now() + timedelta(days=3650)

        payload: dict[str, Any] = {
            "username": user.remna_name,
            "status": "ACTIVE",
            "expireAt": expire_at_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "trafficLimitStrategy": traffic_limit_strategy,
            "trafficLimitBytes": traffic_limit_bytes,
        }
        if user.telegram_id:
            payload["telegramId"] = user.telegram_id
        if user.email:
            payload["email"] = user.email
        if user.remna_description:
            payload["description"] = user.remna_description
        if tag:
            payload["tag"] = tag
        if device_limit is not None:
            payload["hwidDeviceLimit"] = device_limit
        if active_squads:
            payload["activeInternalSquads"] = active_squads
        if external_squad:
            payload["externalSquadUuid"] = external_squad

        return payload

    def _build_v3_update_payload(  # noqa: C901
        self,
        user: UserDto,
        id: int,
        plan: Optional[PlanSnapshotDto],
        subscription: Optional[SubscriptionDto],
    ) -> dict[str, Any]:
        traffic_limit_strategy = "NO_RESET"
        traffic_limit_bytes = 0
        device_limit = None
        tag = None
        active_squads: list[str] = []
        external_squad = None

        if subscription:
            expire_at_dt = subscription.expire_at
            status = (
                SubscriptionStatus.DISABLED
                if subscription.status == SubscriptionStatus.DISABLED
                else SubscriptionStatus.ACTIVE
            )
            traffic_limit_strategy = subscription.traffic_limit_strategy.value
            traffic_limit_bytes = gb_to_bytes(subscription.traffic_limit)
            device_limit = subscription.device_limit
            tag = subscription.tag
            active_squads = [str(s) for s in subscription.internal_squads]
            external_squad = (
                str(subscription.external_squad) if subscription.external_squad else None
            )
        elif plan:
            expire_at_dt = days_to_datetime(plan.duration)
            status = SubscriptionStatus.ACTIVE
            traffic_limit_strategy = plan.traffic_limit_strategy.value
            traffic_limit_bytes = gb_to_bytes(plan.traffic_limit)
            device_limit = plan.device_limit
            tag = plan.tag
            active_squads = [str(s) for s in plan.internal_squads]
            external_squad = str(plan.external_squad) if plan.external_squad else None
        else:
            raise ValueError("Either 'plan' or 'subscription' must be provided")

        # Identify the panel user by `id` only. The v3 PATCH accepts either id or username as the
        # lookup key; sending both lets the panel pick one we did not verify.
        payload: dict[str, Any] = {
            "id": id,
            "status": status.value,
            "trafficLimitStrategy": traffic_limit_strategy,
            "trafficLimitBytes": traffic_limit_bytes,
        }
        # v3 rejects a past expireAt ("Expiration date cannot be in the past"); for an already
        # expired subscription keep the panel's date instead of failing the whole update.
        if expire_at_dt > datetime_now():
            payload["expireAt"] = expire_at_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        if user.telegram_id:
            payload["telegramId"] = user.telegram_id
        if user.email:
            payload["email"] = user.email
        if user.remna_description:
            payload["description"] = user.remna_description
        if tag:
            payload["tag"] = tag
        if device_limit is not None:
            payload["hwidDeviceLimit"] = device_limit
        if active_squads:
            payload["activeInternalSquads"] = active_squads
        if external_squad:
            payload["externalSquadUuid"] = external_squad

        return payload
