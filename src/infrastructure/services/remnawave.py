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
    ApiError,
    ApiErrorResponse,
    AuthenticationError,
    ConflictError,
    NotFoundError,
)
from remnapy.models import (
    CreateUserRequestDto,
    DropByUserUuids,
    DropConnectionsRequestDto,
    GetMetadataResponseDto,
    TargetAllNodes,
    UpdateUserRequestDto,
    UserResponseDto,
)
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
from src.core.utils.converters import days_to_datetime, gb_to_bytes
from src.core.utils.time import datetime_now


class RemnawaveImpl(Remnawave):
    def __init__(self, sdk: RemnawaveSDK) -> None:
        self.sdk = sdk
        self._panel_version: Optional[Version] = None

    @property
    def is_v3(self) -> bool:
        if self._panel_version is not None:
            return self._panel_version.major >= 3
        return True

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
        logger.info(
            f"Successfully connected to Remnawave panel (version: {panel_version}, "
            f"is_v3: {self.is_v3})"
        )
        return panel_version

    @staticmethod
    def _normalize_user_dict(data: dict[str, Any]) -> dict[str, Any]:
        """Normalizes user dictionary so UserResponseDto validation succeeds in v2 and v3."""
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

    async def create_user(
        self,
        user: UserDto,
        plan: Optional[PlanSnapshotDto] = None,
        subscription: Optional[SubscriptionDto] = None,
    ) -> UserResponseDto:
        if user.telegram_id:
            try:
                existing_users = await self.get_users_by_telegram_id(user.telegram_id)
                if existing_users:
                    logger.info(
                        f"RemnaUser for telegram_id '{user.telegram_id}' already exists in panel "
                        f"(ID: '{existing_users[0].id}'). Updating instead of creating."
                    )
                    return await self.update_user(
                        user=user,
                        id=existing_users[0].id,
                        plan=plan,
                        subscription=subscription,
                        reset_traffic=True,
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to check existing RemnaUser for telegram_id "
                    f"'{user.telegram_id}': '{e}'"
                )

        if self.is_v3 and self.sdk._client:
            payload = self._build_v3_create_payload(user, plan, subscription)
            response = await self.sdk._client.post("/users", json=payload)
            if response.status_code == 409:
                logger.warning(
                    f"RemnaUser '{user.remna_name}' already exists in panel (409 Conflict)"
                )
                raise ConflictError(
                    status_code=409,
                    error=ApiErrorResponse(
                        message=f"User {user.remna_name} already exists",
                        code="USER_ALREADY_EXISTS",
                    ),
                )

            if response.status_code not in {200, 201}:
                logger.error(
                    f"Failed to create RemnaUser '{user.remna_name}' in v3: "
                    f"status {response.status_code}, body: {response.text}"
                )
                response.raise_for_status()

            remna_user = self._parse_user_response(response.json())
            logger.info(
                f"RemnaUser '{remna_user.username}' created successfully (v3). "
                f"ID: '{remna_user.id}', telegram_id: '{remna_user.telegram_id}'"
            )
            return remna_user

        request_dto = self._build_create_request(user, plan, subscription)
        try:
            remna_user = await self.sdk.users.create_user(request_dto)
            logger.info(
                f"RemnaUser '{remna_user.username}' created successfully. "
                f"ID: '{remna_user.id}', telegram_id: '{remna_user.telegram_id}'"
            )
            return remna_user
        except ConflictError:
            logger.warning(f"RemnaUser '{request_dto.username}' already exists in panel")
            raise

    async def update_user(
        self,
        user: UserDto,
        id: int,
        plan: Optional[PlanSnapshotDto] = None,
        subscription: Optional[SubscriptionDto] = None,
        reset_traffic: bool = False,
    ) -> UserResponseDto:
        if self.is_v3 and self.sdk._client:
            payload = self._build_v3_update_payload(user, id, plan, subscription)
            response = await self.sdk._client.patch("/users", json=payload)
            if response.status_code == 404:
                logger.warning(f"RemnaUser '{user.remna_name}' with ID '{id}' not found (404)")
                raise NotFoundError(
                    status_code=404,
                    error=ApiErrorResponse(
                        message=f"User {id} not found",
                        code="USER_NOT_FOUND",
                    ),
                )
            if response.status_code not in {200, 201}:
                logger.error(
                    f"Failed to update RemnaUser '{id}' in v3: "
                    f"status {response.status_code}, body: {response.text}"
                )
                response.raise_for_status()

            remna_user = self._parse_user_response(response.json())
            logger.info(
                f"RemnaUser '{remna_user.username}' updated successfully (v3). "
                f"ID: '{remna_user.id}', telegram_id: '{remna_user.telegram_id}'"
            )
            if reset_traffic:
                await self.reset_traffic(id)
            return remna_user

        uuid = getattr(user, "remna_uuid", None)
        username = user.remna_name
        if not uuid and id and id > 0:
            existing = await self.get_user_by_id(id)
            if existing:
                uuid = existing.uuid
                username = existing.username

        request_dto = self._build_update_request(
            user=user,
            id=id,
            plan=plan,
            subscription=subscription,
            uuid=uuid,
            username=username,
        )
        try:
            remna_user = await self.sdk.users.update_user(request_dto)
            logger.info(
                f"RemnaUser '{remna_user.username}' updated successfully. "
                f"ID: '{remna_user.id}', telegram_id: '{remna_user.telegram_id}'"
            )
        except (NotFoundError, ApiError) as e:
            logger.warning(f"RemnaUser '{request_dto.username}' with ID '{id}' not found: {e}")
            raise NotFoundError(
                status_code=404,
                error=ApiErrorResponse(
                    message=f"User {id} not found",
                    code="USER_NOT_FOUND",
                ),
            )

        if reset_traffic:
            await self.reset_traffic(id)

        return remna_user

    async def enable_user(self, id: int) -> None:
        if self.is_v3 and self.sdk._client:
            response = await self.sdk._client.post(f"/users/{id}/actions/enable")
            if response.status_code == 404:
                logger.debug(f"RemnaUser '{id}' not found in panel")
                raise NotFoundError(
                    status_code=404,
                    error=ApiErrorResponse(
                        message=f"User {id} not found",
                        code="USER_NOT_FOUND",
                    ),
                )
            if response.status_code not in {200, 201, 204}:
                response.raise_for_status()
            logger.info(f"RemnaUser '{id}' enabled successfully (v3)")
            return

        try:
            await self.sdk.users.enable_user(id)
            logger.info(f"RemnaUser '{id}' enabled successfully")
        except NotFoundError:
            logger.debug(f"RemnaUser '{id}' not found in panel")
            raise

    async def disable_user(self, id: int) -> None:
        if self.is_v3 and self.sdk._client:
            response = await self.sdk._client.post(f"/users/{id}/actions/disable")
            if response.status_code == 404:
                logger.debug(f"RemnaUser '{id}' not found in panel")
                raise NotFoundError(
                    status_code=404,
                    error=ApiErrorResponse(
                        message=f"User {id} not found",
                        code="USER_NOT_FOUND",
                    ),
                )
            if response.status_code not in {200, 201, 204}:
                response.raise_for_status()
            logger.info(f"RemnaUser '{id}' disabled successfully (v3)")
            return

        try:
            await self.sdk.users.disable_user(id)
            logger.info(f"RemnaUser '{id}' disabled successfully")
        except NotFoundError:
            logger.debug(f"RemnaUser '{id}' not found in panel")
            raise

    async def delete_user(self, id: int) -> bool:
        if self.is_v3 and self.sdk._client:
            del_response = await self.sdk._client.delete(f"/users/{id}")
            if del_response.status_code == 404:
                logger.debug(f"RemnaUser '{id}' not found in panel")
                return False
            if del_response.status_code in {200, 204}:
                logger.info(f"RemnaUser '{id}' deleted successfully (v3)")
                return True
            logger.warning(f"Failed to delete RemnaUser '{id}': status {del_response.status_code}")
            return False

        try:
            v2_del_response = await self.sdk.users.delete_user(id)
        except NotFoundError:
            logger.debug(f"RemnaUser '{id}' not found in panel")
            return False

        if v2_del_response.is_deleted:
            logger.info(f"RemnaUser '{id}' deleted successfully")
        else:
            logger.warning(f"Failed to delete RemnaUser '{id}'")

        return bool(v2_del_response.is_deleted)

    async def get_user_by_id(self, id: int) -> Optional[UserResponseDto]:
        if not id or id <= 0:
            return None

        if self.sdk._client:
            if self.is_v3:
                try:
                    path = f"/users/{id}"
                    response = await self.sdk._client.get(path)
                    if response.status_code == 200:
                        remna_user = self._parse_user_response(response.json())
                        logger.info(f"Fetched RemnaUser '{id}' from panel")
                        return remna_user
                    if response.status_code in {400, 404}:
                        legacy = await self.sdk._client.get(f"/users/by-id/{id}")
                        if legacy.status_code == 200:
                            return self._parse_user_response(legacy.json())
                except Exception as e:
                    logger.debug(f"Error fetching RemnaUser '{id}': {e}")
            else:
                try:
                    response = await self.sdk._client.get(f"/users/by-id/{id}")
                    if response.status_code == 200:
                        remna_user = self._parse_user_response(response.json())
                        logger.info(f"Fetched RemnaUser '{id}' from panel")
                        return remna_user
                except Exception:
                    pass

                try:
                    users_res = await self.sdk._client.get("/users", params={"size": 200})
                    if users_res.status_code == 200:
                        for u_dto in self._parse_users_list(users_res.json()):
                            if u_dto.id == id:
                                logger.info(f"Fetched RemnaUser '{id}' from panel via 2.8.x list")
                                return u_dto
                except Exception as e:
                    logger.debug(f"2.8.x lookup for user id '{id}' failed: {e}")

        try:
            remna_user = await self.sdk.users.get_user_by_id(id)
            logger.info(f"Fetched RemnaUser '{id}' from panel via SDK")
            return remna_user
        except (NotFoundError, ApiError, Exception) as e:
            logger.debug(f"RemnaUser '{id}' not found in panel: {e}")
            return None

    async def get_users_by_telegram_id(self, telegram_id: int) -> list[UserResponseDto]:
        if not self.sdk._client:
            return []

        if self.is_v3:
            try:
                filters_param = json.dumps([{"id": "telegramId", "value": telegram_id}])
                response = await self.sdk._client.get("/users", params={"filters": filters_param})
                if response.status_code == 200:
                    remna_users = self._parse_users_list(response.json())
                    if remna_users:
                        logger.debug(
                            f"Fetched {len(remna_users)} RemnaUsers for telegram_id "
                            f"'{telegram_id}' (v3 filter)"
                        )
                        return remna_users
            except Exception as e:
                logger.debug(f"Error querying v3 users for telegram_id '{telegram_id}': {e}")

        try:
            response = await self.sdk._client.get(f"/users/by-username/rs_{telegram_id}")
            if response.status_code == 200:
                user = self._parse_user_response(response.json())
                if user:
                    return [user]
        except Exception:
            pass

        try:
            response = await self.sdk._client.get(f"/users/by-telegram-id/{telegram_id}")
            if response.status_code == 200:
                remna_users = self._parse_users_list(response.json())
                logger.debug(
                    f"Fetched {len(remna_users)} RemnaUsers for telegram_id '{telegram_id}'"
                )
                return remna_users
        except Exception as e:
            logger.debug(f"Error querying users/by-telegram-id for '{telegram_id}': {e}")

        try:
            response = await self.sdk._client.get("/users", params={"telegramId": telegram_id})
            if response.status_code == 200:
                remna_users = self._parse_users_list(response.json())
                return remna_users
        except Exception:
            pass

        return []

    async def get_all_users(self, limit: int, offset: int) -> list[UserResponseDto]:
        if self.sdk._client:
            response = await self.sdk._client.get("/users", params={"start": offset, "size": limit})
            if response.status_code == 200:
                users = self._parse_users_list(response.json())
                logger.debug(f"Fetched {len(users)} RemnaUsers (limit={limit}, offset={offset})")
                return users

        response_sdk = await self.sdk.users.get_all_users(start=offset, size=limit)
        logger.debug(
            f"Fetched {len(response_sdk.users)} RemnaUsers (limit={limit}, offset={offset})"
        )
        return response_sdk.users

    async def get_user_by_email(self, email: str) -> list[UserResponseDto]:
        if not self.sdk._client:
            return []

        if self.is_v3:
            try:
                filters_param = json.dumps([{"id": "email", "value": email}])
                response = await self.sdk._client.get("/users", params={"filters": filters_param})
                if response.status_code == 200:
                    remna_users = self._parse_users_list(response.json())
                    if remna_users:
                        logger.debug(
                            f"Fetched {len(remna_users)} RemnaUsers for email '{email}' (v3 filter)"
                        )
                        return remna_users
            except Exception:
                pass

        try:
            response = await self.sdk._client.get(f"/users/by-email/{email}")
            if response.status_code == 200:
                remna_users = self._parse_users_list(response.json())
                logger.debug(f"Fetched {len(remna_users)} RemnaUsers for email '{email}'")
                return remna_users
        except Exception:
            pass

        return []

    async def get_devices(self, id: Union[int, UUID, str]) -> list[HwidDeviceDto]:
        if not self.sdk._client or not id or id == 0 or id == "0":
            return []

        target_param = str(id)
        if not self.is_v3 and str(id).isdigit():
            user = await self.get_user_by_id(int(id))
            if user and user.uuid:
                target_param = str(user.uuid)

        if self.is_v3 and not str(id).isdigit():
            user = await self.get_user_by_id(id)
            if user and user.id:
                target_param = str(user.id)

        try:
            response = await self.sdk._client.get(f"/hwid/devices/{target_param}")
            if response.status_code in {400, 404}:
                return []
            if response.status_code != 200:
                logger.warning(
                    f"Failed to fetch devices for RemnaUser '{id}': status {response.status_code}"
                )
                return []
            data = response.json().get("response", {})
            devices_raw = data.get("devices", [])
            devices = [HwidDeviceDto.model_validate(d) for d in devices_raw]
            logger.debug(f"Fetched {len(devices)} devices for RemnaUser '{id}'")
            return devices
        except Exception as e:
            logger.warning(f"Error fetching devices for RemnaUser '{id}': {e}")
            return []

    async def delete_device(self, user_id: Union[int, UUID, str], hwid: str) -> Optional[int]:
        if not self.sdk._client or not user_id or user_id == 0 or user_id == "0":
            return None

        if self.is_v3:
            payload: dict[str, Any] = {"hwid": hwid}
            if str(user_id).isdigit():
                payload["userId"] = int(user_id)
            else:
                user = await self.get_user_by_id(user_id)
                payload["userId"] = user.id if user else user_id
            response = await self.sdk._client.post("/hwid/devices/delete", json=payload)
            if response.status_code in {400, 404}:
                logger.debug(f"RemnaUser '{user_id}' not found in panel")
                return None
            if response.status_code in {200, 201}:
                data = response.json().get("response", {})
                total = int(data.get("total", 0))
                logger.info(
                    f"Deleted HWID device '{hwid}' for RemnaUser '{user_id}'. "
                    f"Total devices now: {total}"
                )
                return total
            return None

        payload = {"hwid": hwid}
        if not str(user_id).isdigit():
            payload["userUuid"] = str(user_id)
        else:
            user = await self.get_user_by_id(int(user_id))
            payload["userUuid"] = str(user.uuid) if user and user.uuid else str(user_id)

        try:
            response = await self.sdk._client.post("/hwid/devices/delete", json=payload)
            if response.status_code in {400, 404}:
                logger.debug(f"RemnaUser '{user_id}' not found in panel")
                return None
            if response.status_code in {200, 201}:
                data = response.json().get("response", {})
                total = int(data.get("total", 0))
                logger.info(
                    f"Deleted HWID device '{hwid}' for RemnaUser '{user_id}'. "
                    f"Total devices now: {total}"
                )
                return total
            return None
        except Exception as e:
            logger.warning(f"Failed to delete HWID device '{hwid}' for RemnaUser '{user_id}': {e}")
            return None

    async def delete_all_devices(self, user_id: Union[int, UUID, str]) -> None:
        if not self.sdk._client:
            return

        if self.is_v3:
            payload: dict[str, Any] = {}
            if str(user_id).isdigit():
                payload["userId"] = int(user_id)
            else:
                user = await self.get_user_by_id(user_id)
                payload["userId"] = user.id if user else user_id
            await self.sdk._client.post("/hwid/devices/delete-all", json=payload)
            return

        payload = {}
        if not str(user_id).isdigit():
            payload["userUuid"] = str(user_id)
        else:
            user = await self.get_user_by_id(int(user_id))
            payload["userUuid"] = str(user.uuid) if user and user.uuid else str(user_id)

        try:
            await self.sdk._client.post("/hwid/devices/delete-all", json=payload)
            logger.info(f"Deleted all HWID devices for RemnaUser '{user_id}'")
        except Exception as e:
            logger.warning(f"Failed to delete all HWID devices for RemnaUser '{user_id}': {e}")

    async def drop_connections(self, user_id: int) -> None:
        if self.is_v3 and self.sdk._client:
            try:
                await self.sdk._client.post(
                    "/connections/drop",
                    json={
                        "dropBy": {"by": "userIds", "userIds": [user_id]},
                        "targetNodes": {"target": "allNodes"},
                    },
                )
                logger.info(f"Dropped connections for RemnaUser '{user_id}' (v3)")
                return
            except Exception as e:
                logger.warning(f"Failed to drop connections for RemnaUser '{user_id}' in v3: {e}")

        try:
            await self.sdk.ip_control.drop_connections(
                body=DropConnectionsRequestDto(
                    drop_by=DropByUserUuids(user_uuids=[user_id]),
                    target_nodes=TargetAllNodes(),
                )
            )
            logger.info(f"Dropped connections for RemnaUser '{user_id}'")
        except Exception as e:
            logger.warning(f"Failed to drop connections for RemnaUser '{user_id}': {e}")

    async def reset_traffic(self, id: int) -> Optional[UserResponseDto]:
        if self.is_v3 and self.sdk._client:
            response = await self.sdk._client.post(f"/users/{id}/actions/reset-traffic")
            if response.status_code == 404:
                logger.debug(f"RemnaUser '{id}' not found in panel")
                return None
            if response.status_code in {200, 201}:
                remna_user = self._parse_user_response(response.json())
                logger.info(f"Traffic for RemnaUser '{remna_user.id}' reset successfully (v3)")
                return remna_user

        try:
            remna_user = await self.sdk.users.reset_user_traffic(id)
            logger.info(f"Traffic for RemnaUser '{remna_user.id}' reset successfully")
            return remna_user
        except NotFoundError:
            logger.debug(f"RemnaUser '{id}' not found in panel")
            return None

    async def revoke_subscription(self, id: int) -> None:
        if self.is_v3 and self.sdk._client:
            response = await self.sdk._client.post(f"/users/{id}/actions/revoke")
            if response.status_code == 404:
                logger.debug(f"RemnaUser '{id}' not found in panel")
                return
            if response.status_code in {200, 201, 204}:
                logger.info(f"Subscription for RemnaUser '{id}' revoked successfully (v3)")
                return

        try:
            await self.sdk.users.revoke_user_subscription(id)
            logger.info(f"Subscription for RemnaUser '{id}' revoked successfully")
        except NotFoundError:
            logger.debug(f"RemnaUser '{id}' not found in panel")

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
        expire_at_dt = None
        traffic_limit_strategy = "NO_RESET"
        traffic_limit_bytes = 0
        device_limit = None
        tag = None
        active_squads = []
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

        # Format ISO timestamp in UTC with trailing Z
        expire_at_iso = expire_at_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        payload: dict[str, Any] = {
            "username": user.remna_name,
            "status": "ACTIVE",
            "expireAt": expire_at_iso,
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

    def _build_v3_update_payload(
        self,
        user: UserDto,
        id: int,
        plan: Optional[PlanSnapshotDto],
        subscription: Optional[SubscriptionDto],
    ) -> dict[str, Any]:
        expire_at_dt = None
        status = SubscriptionStatus.ACTIVE
        traffic_limit_strategy = "NO_RESET"
        traffic_limit_bytes = 0
        device_limit = None
        tag = None
        active_squads = []
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

        expire_at_iso = expire_at_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        payload: dict[str, Any] = {
            "id": id,
            "username": user.remna_name,
            "status": status.value,
            "expireAt": expire_at_iso,
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

    def _build_create_request(
        self,
        user: UserDto,
        plan: Optional[PlanSnapshotDto],
        subscription: Optional[SubscriptionDto],
    ) -> CreateUserRequestDto:
        if subscription:
            return CreateUserRequestDto(
                username=user.remna_name,
                telegram_id=user.telegram_id,
                expire_at=subscription.expire_at,
                traffic_limit_strategy=subscription.traffic_limit_strategy,
                traffic_limit_bytes=gb_to_bytes(subscription.traffic_limit),
                hwid_device_limit=subscription.device_limit,
                description=user.remna_description,
                email=user.email,
                tag=subscription.tag,
                active_internal_squads=subscription.internal_squads,
                external_squad_uuid=subscription.external_squad,
            )

        if plan:
            return CreateUserRequestDto(
                username=user.remna_name,
                telegram_id=user.telegram_id,
                expire_at=days_to_datetime(plan.duration),
                traffic_limit_strategy=plan.traffic_limit_strategy,
                traffic_limit_bytes=gb_to_bytes(plan.traffic_limit),
                hwid_device_limit=plan.device_limit,
                description=user.remna_description,
                email=user.email,
                tag=plan.tag,
                active_internal_squads=plan.internal_squads,
                external_squad_uuid=plan.external_squad,
            )

        return CreateUserRequestDto(
            username=user.remna_name,
            telegram_id=user.telegram_id,
            expire_at=datetime_now() + timedelta(days=3650),
            description=user.remna_description,
            email=user.email,
        )

    def _build_update_request(
        self,
        user: UserDto,
        id: int,
        plan: Optional[PlanSnapshotDto],
        subscription: Optional[SubscriptionDto],
        uuid: Optional[Any] = None,
        username: Optional[str] = None,
    ) -> UpdateUserRequestDto:
        effective_uuid = uuid or getattr(user, "remna_uuid", None)
        effective_username = username or user.remna_name
        if subscription:
            return UpdateUserRequestDto(
                uuid=effective_uuid,
                username=effective_username if not effective_uuid else None,
                telegram_id=user.telegram_id,
                expire_at=subscription.expire_at,
                status=(
                    SubscriptionStatus.DISABLED
                    if subscription.status == SubscriptionStatus.DISABLED
                    else SubscriptionStatus.ACTIVE
                ),
                traffic_limit_strategy=subscription.traffic_limit_strategy,
                traffic_limit_bytes=gb_to_bytes(subscription.traffic_limit),
                hwid_device_limit=subscription.device_limit,
                description=user.remna_description,
                email=user.email,
                tag=subscription.tag,
                active_internal_squads=subscription.internal_squads,
                external_squad_uuid=subscription.external_squad,
            )

        if plan:
            return UpdateUserRequestDto(
                uuid=effective_uuid,
                username=effective_username if not effective_uuid else None,
                telegram_id=user.telegram_id,
                expire_at=days_to_datetime(plan.duration),
                status=SubscriptionStatus.ACTIVE,
                traffic_limit_strategy=plan.traffic_limit_strategy,
                traffic_limit_bytes=gb_to_bytes(plan.traffic_limit),
                hwid_device_limit=plan.device_limit,
                description=user.remna_description,
                email=user.email,
                tag=plan.tag,
                active_internal_squads=plan.internal_squads,
                external_squad_uuid=plan.external_squad,
            )

        raise ValueError("Either 'plan' or 'subscription' must be provided")
