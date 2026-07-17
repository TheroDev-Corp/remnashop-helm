from dataclasses import dataclass

from fastapi import HTTPException, status

from src.application.common import Interactor
from src.application.common.dao import UserDao
from src.application.common.dao.auth import AuthSessionDao
from src.application.common.password_hasher import PasswordHasher
from src.application.common.policy import Permission
from src.application.common.uow import UnitOfWork
from src.application.dto import UserDto


@dataclass
class ChangePasswordDto:
    current_password: str
    new_password: str


class ChangePassword(Interactor[ChangePasswordDto, UserDto]):
    required_permission = Permission.PUBLIC

    def __init__(
        self,
        uow: UnitOfWork,
        user_dao: UserDao,
        auth_session: AuthSessionDao,
        password_hasher: PasswordHasher,
    ) -> None:
        self.uow = uow
        self.user_dao = user_dao
        self.auth_session = auth_session
        self.password_hasher = password_hasher

    async def _execute(self, actor: UserDto, data: ChangePasswordDto) -> UserDto:
        if not self.password_hasher.verify(data.current_password, actor.password_hash or ""):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Current password is invalid",
            )
        if self.password_hasher.verify(data.new_password, actor.password_hash or ""):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="New password must be different from current password",
            )

        actor.password_hash = self.password_hasher.hash(data.new_password)

        async with self.uow:
            updated = await self.user_dao.update(actor)
            if not updated:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found during password update",
                )
            await self.uow.commit()

        await self.auth_session.revoke_all_user_tokens(actor.id)
        return updated
