from dataclasses import dataclass

from fastapi import HTTPException, status

from src.application.common import Interactor
from src.application.common.dao import UserDao
from src.application.common.password_hasher import PasswordHasher
from src.application.dto import UserDto


@dataclass
class LoginEmailUserDto:
    email: str
    password: str


class LoginEmailUser(Interactor[LoginEmailUserDto, UserDto]):
    required_permission = None

    def __init__(self, user_dao: UserDao, password_hasher: PasswordHasher) -> None:
        self.user_dao = user_dao
        self.password_hasher = password_hasher

    async def _execute(self, actor: UserDto, data: LoginEmailUserDto) -> UserDto:
        user = await self.user_dao.get_by_email(data.email)
        password_hash = user.password_hash if (user and user.password_hash) else ""
        password_ok = self.password_hasher.verify(data.password, password_hash)
        if not user or not user.password_hash or not password_ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )
        if user.is_blocked:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is blocked")
        return user
