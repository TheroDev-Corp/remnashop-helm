import asyncio
from dataclasses import fields, is_dataclass
from datetime import timedelta
from typing import Optional, Union

from loguru import logger
from packaging.version import Version
from remnapy import RemnawaveSDK
from remnapy.exceptions import AuthenticationError, ConflictError, NotFoundError
from remnapy.models import (
    CreateUserRequestDto,
    DeleteUserAllHwidDeviceRequestDto,
    DeleteUserHwidDeviceRequestDto,
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

        logger.info(f"Successfully connected to Remnawave panel (version: {panel_version})")
        return panel_version

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
                        f"(UUID: '{existing_users[0].uuid}'). Updating instead of creating."
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
                    f"Failed to check existing RemnaUser for telegram_id '{user.telegram_id}': '{e}'"
                )

        request_dto = self._build_create_request(user, plan, subscription)

        try:
            remna_user = await self.sdk.users.create_user(request_dto)
            logger.info(
                f"RemnaUser '{remna_user.username}' created successfully. "
                f"UUID: '{remna_user.uuid}', telegram_id: '{remna_user.telegram_id}'"
            )
            return remna_user
        except ConflictError:
            logger.warning(
                f"RemnaUser '{request_dto.username}' with UUID '{request_dto.uuid}' "
                f"already exists in panel"
            )
            raise

    async def update_user(
        self,
        user: UserDto,
        id: int,
        plan: Optional[PlanSnapshotDto] = None,
        subscription: Optional[SubscriptionDto] = None,
        reset_traffic: bool = False,
    ) -> UserResponseDto:
        request_dto = self._build_update_request(user, id, plan, subscription)

        try:
            remna_user = await self.sdk.users.update_user(request_dto)
            logger.info(
                f"RemnaUser '{remna_user.username}' updated successfully. "
                f"ID: '{remna_user.id}', telegram_id: '{remna_user.telegram_id}'"
            )
        except NotFoundError:
            logger.warning(
                f"RemnaUser '{request_dto.username}' with ID '{id}' not found"
            )
            raise

        if reset_traffic:
            await self.reset_traffic(id)

        return remna_user

    async def enable_user(self, id: int) -> None:
        try:
            await self.sdk.users.enable_user(id)
            logger.info(f"RemnaUser '{id}' enabled successfully")
        except NotFoundError:
            logger.debug(f"RemnaUser '{id}' not found in panel")
            raise

    async def disable_user(self, id: int) -> None:
        try:
            await self.sdk.users.disable_user(id)
            logger.info(f"RemnaUser '{id}' disabled successfully")
        except NotFoundError:
            logger.debug(f"RemnaUser '{id}' not found in panel")
            raise

    async def delete_user(self, id: int) -> bool:
        try:
            response = await self.sdk.users.delete_user(id)
        except NotFoundError:
            logger.debug(f"RemnaUser '{id}' not found in panel")
            return False

        if response.is_deleted:
            logger.info(f"RemnaUser '{id}' deleted successfully")
        else:
            logger.warning(f"Failed to delete RemnaUser '{id}'")

        return response.is_deleted

    async def get_user_by_id(self, id: int) -> Optional[UserResponseDto]:
        if not id or id <= 0:
            return None
        try:
            remna_user = await self.sdk.users.get_user_by_id(id)
            logger.info(f"Fetched RemnaUser '{id}' from panel")
            return remna_user
        except (NotFoundError, Exception) as e:
            logger.debug(f"RemnaUser '{id}' not found in panel: {e}")
            return None

    async def get_users_by_telegram_id(self, telegram_id: int) -> list[UserResponseDto]:
        if not self.sdk._client:
            return []
        response = await self.sdk._client.get("/users", params={"telegramId": telegram_id})
        if response.status_code != 200:
            logger.warning(
                f"Failed to fetch RemnaUsers for telegram_id '{telegram_id}': status {response.status_code}"
            )
            return []
        data = response.json().get("response", {})
        users_raw = data.get("users", [])
        remna_users = [UserResponseDto.model_validate(u) for u in users_raw]
        logger.debug(f"Fetched {len(remna_users)} RemnaUsers for telegram_id '{telegram_id}'")
        return remna_users

    async def get_all_users(self, limit: int, offset: int) -> list[UserResponseDto]:
        response = await self.sdk.users.get_all_users(start=offset, size=limit)
        logger.debug(f"Fetched {len(response.users)} RemnaUsers (limit={limit}, offset={offset})")
        return response.users

    async def get_user_by_email(self, email: str) -> list[UserResponseDto]:
        if not self.sdk._client:
            return []
        response = await self.sdk._client.get("/users", params={"email": email})
        if response.status_code != 200:
            logger.warning(
                f"Failed to fetch RemnaUsers for email '{email}': status {response.status_code}"
            )
            return []
        data = response.json().get("response", {})
        users_raw = data.get("users", [])
        remna_users = [UserResponseDto.model_validate(u) for u in users_raw]
        logger.debug(f"Fetched {len(remna_users)} RemnaUsers for email '{email}'")
        return remna_users

    async def get_devices(self, id: int) -> list[HwidDeviceDto]:
        if not self.sdk._client:
            return []
        response = await self.sdk._client.get(f"/hwid/devices/{id}")
        if response.status_code == 404:
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

    async def delete_device(self, user_id: int, hwid: str) -> Optional[int]:
        try:
            response = await self.sdk.hwid.delete_hwid_to_user(
                DeleteUserHwidDeviceRequestDto(user_uuid=user_id, hwid=hwid)
            )
            logger.info(
                f"Deleted HWID device '{hwid}' for RemnaUser '{user_id}'. "
                f"Total devices now: {response.total}"
            )
        except NotFoundError:
            logger.debug(f"RemnaUser '{user_id}' not found in panel")
            return None

        return int(response.total)

    async def delete_all_devices(self, user_id: int) -> None:
        try:
            result = await self.sdk.hwid.delete_all_hwid_user(
                DeleteUserAllHwidDeviceRequestDto(user_uuid=user_id)
            )
        except NotFoundError:
            logger.debug(f"RemnaUser '{user_id}' not found in panel")
            return
        logger.info(f"Deleted all HWID devices ({result.total}) for RemnaUser '{user_id}'")

    async def drop_connections(self, user_id: int) -> None:
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
        try:
            remna_user = await self.sdk.users.reset_user_traffic(id)
            logger.info(f"Traffic for RemnaUser '{remna_user.id}' reset successfully")
            return remna_user
        except NotFoundError:
            logger.debug(f"RemnaUser '{id}' not found in panel")
            return None

    async def revoke_subscription(self, id: int) -> None:
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

    def _build_create_request(
        self,
        user: UserDto,
        plan: Optional[PlanSnapshotDto],
        subscription: Optional[SubscriptionDto],
    ) -> CreateUserRequestDto:
        if subscription:
            return CreateUserRequestDto(
                uuid=subscription.user_remna_id,
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
    ) -> UpdateUserRequestDto:
        if subscription:
            return UpdateUserRequestDto(
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
