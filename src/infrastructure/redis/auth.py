from typing import Optional

from redis.asyncio import Redis

from src.infrastructure.redis.key_builder import serialize_storage_key
from src.infrastructure.redis.keys import RefreshTokenKey


class RedisAuthRepository:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def store_refresh_token(self, token: str, user_id: int, ttl: int) -> None:
        key = serialize_storage_key(RefreshTokenKey(token=token))
        await self.redis.setex(key, ttl, str(user_id))

    async def get_user_id_by_refresh_token(self, token: str) -> Optional[int]:
        key = serialize_storage_key(RefreshTokenKey(token=token))
        value = await self.redis.get(key)
        if value is None:
            return None
        return int(value)

    async def revoke_refresh_token(self, token: str) -> None:
        key = serialize_storage_key(RefreshTokenKey(token=token))
        await self.redis.delete(key)

    async def get_and_revoke_refresh_token(self, token: str) -> Optional[int]:
        key = serialize_storage_key(RefreshTokenKey(token=token))
        value = await self.redis.getdel(key)
        if value is None:
            return None
        return int(value)
