import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from src.application.common import Interactor, XuiDbReader
from src.application.common.policy import Permission
from src.application.dto import UserDto
from src.application.use_cases.importer.dto import ExportedUserDto
from src.core.constants import IMPORTED_TAG, REMNASHOP_PREFIX, UNLIMITED_EXPIRE_YEAR
from src.core.enums import SubscriptionStatus


class ExportUsersFromXui(Interactor[Path, list[ExportedUserDto]]):
    required_permission = Permission.IMPORTER

    def __init__(self, xui_reader: XuiDbReader) -> None:
        self.xui_reader = xui_reader

    async def _execute(self, actor: UserDto, db_path: Path) -> list[ExportedUserDto]:
        if not db_path.exists():
            raise ValueError(f"File not found at '{db_path}'")

        inbounds = await self.xui_reader.read_inbounds(db_path)

        if not inbounds:
            raise ValueError("No inbounds found in 3X-UI database")

        best_inbound_id = 0
        max_clients = -1
        target_clients = []

        for row_id, settings_raw in inbounds:
            try:
                settings = json.loads(settings_raw)
                clients = settings.get("clients", [])
                if isinstance(clients, list) and len(clients) > max_clients:
                    max_clients = len(clients)
                    best_inbound_id = row_id
                    target_clients = clients
            except json.JSONDecodeError:
                continue

        if best_inbound_id == 0:
            raise ValueError("No valid clients found in any inbound")

        logger.info(
            f"{actor.log} Selected inbound '{best_inbound_id}' with '{max_clients}' clients"
        )

        transformed = [u for u in (self._transform(c) for c in target_clients) if u]

        logger.info(f"{actor.log} Transformed '{len(transformed)}' users from 3X-UI")
        return transformed

    def _transform(self, user: dict[str, Any]) -> Optional[ExportedUserDto]:
        if not user.get("enable"):
            return None

        match = re.search(r"\d+", user.get("email", ""))
        if not match:
            return None

        telegram_id = int(match.group(0))
        expire_at = (
            datetime.fromtimestamp(user.get("expiryTime", 0) / 1000, tz=timezone.utc)
            if user.get("expiryTime")
            else datetime(UNLIMITED_EXPIRE_YEAR, 1, 1, tzinfo=timezone.utc)
        )

        return ExportedUserDto(
            username=f"{REMNASHOP_PREFIX}{telegram_id}",
            telegram_id=telegram_id,
            status=SubscriptionStatus.ACTIVE,
            expire_at=expire_at,
            traffic_limit_bytes=user.get("totalGB", 0),
            hwid_device_limit=user.get("limitIp", 1),
            tag=IMPORTED_TAG,
        )
