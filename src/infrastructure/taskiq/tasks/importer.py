from dataclasses import asdict
from typing import Any, Union
from uuid import UUID

from adaptix import Retort
from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from redis.asyncio import Redis
from remnapy import RemnawaveSDK
from remnapy.exceptions import BadRequestError
from remnapy.models import CreateUserRequestDto

from src.application.use_cases.importer.dto import ExportedUserDto
from src.application.use_cases.remnawave.commands.synchronization import (
    SyncAllUsersFromBot,
    SyncAllUsersFromPanel,
)
from src.infrastructure.redis.keys import SyncBotRunningKey, SyncPanelRunningKey
from src.infrastructure.taskiq.broker import broker


def build_create_user_request(
    user: Union[ExportedUserDto, dict[str, Any]],
    active_internal_squads: list[UUID],
) -> CreateUserRequestDto:
    # The taskiq worker parses arguments by annotation, so users arrive as ExportedUserDto
    # dataclasses; pydantic's model_validate only accepts a dict (from_attributes is off).
    data = dict(user) if isinstance(user, dict) else asdict(user)
    created_user = CreateUserRequestDto.model_validate(data)
    created_user.active_internal_squads = active_internal_squads
    return created_user


@broker.task
@inject(patch_module=True)
async def import_exported_users_task(
    imported_users: list[ExportedUserDto],
    active_internal_squads: list[UUID],
    remnawave_sdk: FromDishka[RemnawaveSDK],
) -> tuple[int, int]:
    logger.info(f"Starting import of '{len(imported_users)}' users")

    success_count = 0
    failed_count = 0

    for user in imported_users:
        try:
            created_user = build_create_user_request(user, active_internal_squads)
            await remnawave_sdk.users.create_user(created_user)
            success_count += 1
        except BadRequestError as error:
            logger.warning(f"User '{user.username}' already exists, skipping. Error: {error}")
            failed_count += 1

        except Exception as exception:
            logger.exception(f"Failed to create user '{user.username}' exception: {exception}")
            failed_count += 1

    logger.info(f"Import completed: '{success_count}' successful, '{failed_count}' failed")
    return success_count, failed_count


@broker.task
@inject(patch_module=True)
async def sync_all_users_from_panel_task(
    retort: FromDishka[Retort],
    redis: FromDishka[Redis],
    sync_all_users: FromDishka[SyncAllUsersFromPanel],
) -> dict[str, int]:
    key = retort.dump(SyncPanelRunningKey())
    try:
        return await sync_all_users.system()
    finally:
        await redis.delete(key)


@broker.task
@inject(patch_module=True)
async def sync_all_users_from_bot_task(
    retort: FromDishka[Retort],
    redis: FromDishka[Redis],
    sync_all_users: FromDishka[SyncAllUsersFromBot],
) -> dict[str, int]:
    key = retort.dump(SyncBotRunningKey())
    try:
        return await sync_all_users.system()
    finally:
        await redis.delete(key)
