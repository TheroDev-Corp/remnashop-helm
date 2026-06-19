import time
from typing import Sequence

from loguru import logger
from redis.asyncio import Redis

from src.core.constants import RECENT_ACTIVITY_STORE_CAP
from src.infrastructure.redis.key_builder import serialize_storage_key
from src.infrastructure.redis.keys import RecentActivityKey


class RedisActivityRepository:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self._key = serialize_storage_key(RecentActivityKey())

    async def touch(self, user_id: int) -> None:
        await self.redis.zadd(self._key, {str(user_id): time.time()})
        # Keep only the most recent entries to bound memory usage.
        await self.redis.zremrangebyrank(self._key, 0, -(RECENT_ACTIVITY_STORE_CAP + 1))
        logger.debug(f"Recorded recent activity for user_id '{user_id}'")

    async def get_recent_ids(
        self,
        limit: int,
        excluded_ids: Sequence[int] = (),
    ) -> list[int]:
        excluded = {str(i) for i in excluded_ids}
        members = await self.redis.zrevrange(self._key, 0, limit + len(excluded) - 1)
        ids = [int(member) for member in members if member not in excluded]
        return ids[:limit]
