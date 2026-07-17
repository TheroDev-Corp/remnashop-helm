from typing import Optional, Protocol, runtime_checkable

from src.application.dto import UserDto
from src.application.dto.user import UserOAuthProviderDto
from src.core.enums import OAuthProvider


@runtime_checkable
class UserOAuthProviderDao(Protocol):
    async def create(self, dto: UserOAuthProviderDto) -> UserOAuthProviderDto: ...

    async def get_by_provider(
        self,
        provider: OAuthProvider,
        provider_id: str,
    ) -> Optional[UserDto]: ...

    async def get_user_providers(self, user_id: int) -> list[UserOAuthProviderDto]: ...

    async def delete(self, user_id: int, provider: OAuthProvider) -> bool: ...
