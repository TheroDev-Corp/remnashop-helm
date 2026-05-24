import asyncio
import hashlib
import hmac
import json
import secrets
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError

from src.application.common import Cryptographer, EventPublisher
from src.application.common.dao import UserDao
from src.application.common.dao.auth import AuthSessionDao
from src.application.common.uow import UnitOfWork
from src.application.dto import UserDto
from src.application.events import UserRegisteredEvent
from src.application.use_cases.referral.commands.attachment import (
    AttachReferral,
    AttachReferralDto,
)
from src.core.config import AppConfig
from src.core.constants import (
    ACCESS_TOKEN_TTL_SECONDS,
    EMAIL_VERIFICATION_CODE_LENGTH,
    PASSWORD_SCRYPT_DKLEN,
    PASSWORD_SCRYPT_N,
    PASSWORD_SCRYPT_P,
    PASSWORD_SCRYPT_R,
    REFRESH_TOKEN_TTL_SECONDS,
    TELEGRAM_AUTH_MAX_AGE_SECONDS,
)
from src.core.enums import AuthType
from src.core.utils.time import datetime_now
from src.web.schemas import (
    AuthResponse,
    ChangeEmailRequest,
    ChangeEmailResponse,
    ChangePasswordRequest,
    ChangePasswordResponse,
    ConfirmEmailVerificationRequest,
    ConfirmEmailVerificationResponse,
    LoginRequest,
    LogoutRequest,
    LogoutResponse,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    RequestEmailVerificationCodeRequest,
    RequestEmailVerificationCodeResponse,
    TelegramAuthRequest,
)

from ._common import CurrentUser, _b64url_decode, _b64url_encode

router = APIRouter(prefix="/auth", tags=["Public - Auth"])


def _hash_password(password: str, key: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password=f"{password}:{key}".encode("utf-8"),
        salt=salt,
        n=PASSWORD_SCRYPT_N,
        r=PASSWORD_SCRYPT_R,
        p=PASSWORD_SCRYPT_P,
        dklen=PASSWORD_SCRYPT_DKLEN,
    )
    return (
        f"scrypt${PASSWORD_SCRYPT_N}${PASSWORD_SCRYPT_R}${PASSWORD_SCRYPT_P}"
        f"${_b64url_encode(salt)}${_b64url_encode(digest)}"
    )


def _verify_password(password: str, password_hash: str, key: str) -> bool:
    try:
        algorithm, n, r, p, salt_b64, digest_b64 = password_hash.split("$", maxsplit=5)
        if algorithm != "scrypt":
            return False

        expected_digest = _b64url_decode(digest_b64)
        check_digest = hashlib.scrypt(
            password=f"{password}:{key}".encode("utf-8"),
            salt=_b64url_decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected_digest),
        )
        return hmac.compare_digest(expected_digest, check_digest)
    except Exception:
        return False


def _generate_email_verification_code() -> str:
    lower = 10 ** (EMAIL_VERIFICATION_CODE_LENGTH - 1)
    upper = (10**EMAIL_VERIFICATION_CODE_LENGTH) - 1
    return str(secrets.randbelow(upper - lower + 1) + lower)


def _hash_email_verification_code(code: str, key: str) -> str:
    payload = f"{code}:{key}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_email_delivery_enabled(config: AppConfig) -> bool:
    return bool(
        config.email.enabled
        and config.email.host
        and config.email.from_email
        and config.email.username.get_secret_value()
        and config.email.password.get_secret_value()
    )


def _send_email_verification_code_sync(
    *,
    config: AppConfig,
    target_email: str,
    verification_code: str,
) -> None:
    message = EmailMessage()
    message["Subject"] = "Oldnet: подтверждение email"
    from_name = config.email.from_name.strip()
    from_email = config.email.from_email.strip()
    message["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    message["To"] = target_email
    message.set_content(
        "Ваш код подтверждения: "
        f"{verification_code}\n\n"
        f"Код действует {config.email.verification_code_ttl_minutes} минут."
    )

    smtp_host = config.email.host
    smtp_port = config.email.port
    smtp_user = config.email.username.get_secret_value()
    smtp_password = config.email.password.get_secret_value()

    if config.email.use_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as client:
            client.login(smtp_user, smtp_password)
            client.send_message(message)
        return

    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as client:
        client.ehlo()
        if config.email.use_tls:
            client.starttls()
            client.ehlo()
        client.login(smtp_user, smtp_password)
        client.send_message(message)


async def _send_email_verification_code(
    *,
    config: AppConfig,
    target_email: str,
    verification_code: str,
) -> None:
    try:
        await asyncio.to_thread(
            _send_email_verification_code_sync,
            config=config,
            target_email=target_email,
            verification_code=verification_code,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to send verification email: {e}",
        ) from e


def _generate_access_token(subject: int, key: str) -> tuple[str, datetime]:
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    issued_at = int(time.time())
    expires_at = issued_at + ACCESS_TOKEN_TTL_SECONDS
    payload = _b64url_encode(
        json.dumps({"sub": subject, "iat": issued_at, "exp": expires_at}).encode("utf-8")
    )

    signature = hmac.new(
        key.encode("utf-8"),
        f"{header}.{payload}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    token = f"{header}.{payload}.{_b64url_encode(signature)}"
    expires_at_dt = datetime.fromtimestamp(expires_at, tz=timezone.utc)
    return token, expires_at_dt


async def _build_auth_response(
    user: UserDto,
    config: AppConfig,
    auth_session: AuthSessionDao,
) -> AuthResponse:
    access_token, expires_at = _generate_access_token(
        subject=user.id,
        key=config.crypt_key.get_secret_value(),
    )
    refresh_token = secrets.token_urlsafe(32)
    refresh_expires_at = datetime.now(tz=timezone.utc) + timedelta(
        seconds=REFRESH_TOKEN_TTL_SECONDS
    )
    await auth_session.store_refresh_token(
        token=refresh_token,
        user_id=user.id,
        ttl=REFRESH_TOKEN_TTL_SECONDS,
    )
    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        refresh_expires_at=refresh_expires_at,
    )


def _verify_telegram_hash(data: dict[str, Any], bot_token: str) -> bool:
    telegram_hash = str(data.get("hash", ""))
    auth_date = int(data.get("auth_date", 0))

    if int(time.time()) - auth_date > TELEGRAM_AUTH_MAX_AGE_SECONDS:
        return False

    data_check = {k: str(v) for k, v in data.items() if k != "hash"}
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data_check.items()))
    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    expected = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, telegram_hash)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
@inject
async def register_public_user(
    body: RegisterRequest,
    config: FromDishka[AppConfig],
    uow: FromDishka[UnitOfWork],
    user_dao: FromDishka[UserDao],
    cryptographer: FromDishka[Cryptographer],
    attach_referral: FromDishka[AttachReferral],
    event_publisher: FromDishka[EventPublisher],
    auth_session: FromDishka[AuthSessionDao],
) -> AuthResponse:
    if await user_dao.get_by_email(body.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")
    if body.referral_code and not await user_dao.get_by_referral_code(body.referral_code):
        body.referral_code = None

    referral_code = await cryptographer.generate_unique_code(user_dao.get_by_referral_code)

    new_user = UserDto(
        telegram_id=None,
        auth_type=AuthType.EMAIL,
        email=body.email,
        password_hash=_hash_password(body.password, config.crypt_key.get_secret_value()),
        username=None,
        name=body.name or body.email.split("@")[0],
        referral_code=referral_code,
        language=config.default_locale,
    )

    async with uow:
        try:
            created = await user_dao.create(new_user)
            await uow.commit()
        except IntegrityError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User with this email already exists",
            ) from e

    referrer = None
    if body.referral_code:
        referrer = await attach_referral.system(
            AttachReferralDto(
                user_id=created.id,
                referral_code=body.referral_code,
            )
        )

    await event_publisher.publish(
        UserRegisteredEvent(
            user_id=created.id,
            telegram_id=created.telegram_id,
            username=created.username,
            name=created.name,
            email=created.email,
            referrer_user_id=referrer.id if referrer else None,
            referrer_telegram_id=referrer.telegram_id if referrer else None,
            referrer_email=referrer.email if referrer else None,
            referrer_username=referrer.username if referrer else None,
            referrer_name=referrer.name if referrer else None,
        )
    )

    return await _build_auth_response(created, config, auth_session)


@router.post("/login", response_model=AuthResponse)
@inject
async def login_public_user(
    body: LoginRequest,
    config: FromDishka[AppConfig],
    user_dao: FromDishka[UserDao],
    auth_session: FromDishka[AuthSessionDao],
) -> AuthResponse:
    user = await user_dao.get_by_email(body.email)
    if not user or not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not _verify_password(body.password, user.password_hash, config.crypt_key.get_secret_value()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is blocked",
        )

    return await _build_auth_response(user, config, auth_session)


@router.post("/refresh", response_model=AuthResponse)
@inject
async def refresh_access_token(
    body: RefreshRequest,
    config: FromDishka[AppConfig],
    user_dao: FromDishka[UserDao],
    auth_session: FromDishka[AuthSessionDao],
) -> AuthResponse:
    user_id = await auth_session.get_and_revoke_refresh_token(body.refresh_token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is blocked",
        )

    return await _build_auth_response(user, config, auth_session)


@router.post("/logout", response_model=LogoutResponse)
@inject
async def logout(
    body: LogoutRequest,
    user: CurrentUser,
    auth_session: FromDishka[AuthSessionDao],
) -> LogoutResponse:
    owner_id = await auth_session.get_user_id_by_refresh_token(body.refresh_token)
    if owner_id is not None and owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token does not belong to current user",
        )
    await auth_session.revoke_refresh_token(body.refresh_token)
    return LogoutResponse(success=True)


@router.post("/telegram", response_model=AuthResponse)
@inject
async def telegram_login(
    body: TelegramAuthRequest,
    config: FromDishka[AppConfig],
    uow: FromDishka[UnitOfWork],
    user_dao: FromDishka[UserDao],
    cryptographer: FromDishka[Cryptographer],
    event_publisher: FromDishka[EventPublisher],
    auth_session: FromDishka[AuthSessionDao],
) -> AuthResponse:
    bot_token = config.bot.token.get_secret_value()
    payload = body.model_dump(exclude_none=True)
    if not _verify_telegram_hash(payload, bot_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram auth data",
        )

    user = await user_dao.get_by_telegram_id(body.id)
    if user:
        if user.is_blocked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is blocked",
            )
        return await _build_auth_response(user, config, auth_session)

    name_parts = [body.first_name]
    if body.last_name:
        name_parts.append(body.last_name)
    name = " ".join(name_parts)

    referral_code = await cryptographer.generate_unique_code(user_dao.get_by_referral_code)

    new_user = UserDto(
        telegram_id=body.id,
        auth_type=AuthType.TELEGRAM,
        username=body.username,
        name=name,
        referral_code=referral_code,
        language=config.default_locale,
    )

    async with uow:
        try:
            created = await user_dao.create(new_user)
            await uow.commit()
        except IntegrityError as e:
            user = await user_dao.get_by_telegram_id(body.id)
            if user:
                return await _build_auth_response(user, config, auth_session)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User creation conflict",
            ) from e

    await event_publisher.publish(
        UserRegisteredEvent(
            user_id=created.id,
            telegram_id=created.telegram_id,
            username=created.username,
            name=created.name,
            email=created.email,
            referrer_user_id=None,
            referrer_telegram_id=None,
            referrer_email=None,
            referrer_username=None,
            referrer_name=None,
        )
    )

    return await _build_auth_response(created, config, auth_session)


@router.post("/telegram/link", response_model=MeResponse)
@inject
async def link_telegram_account(
    body: TelegramAuthRequest,
    user: CurrentUser,
    config: FromDishka[AppConfig],
    uow: FromDishka[UnitOfWork],
    user_dao: FromDishka[UserDao],
) -> MeResponse:
    bot_token = config.bot.token.get_secret_value()
    payload = body.model_dump(exclude_none=True)
    if not _verify_telegram_hash(payload, bot_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram auth data",
        )

    if user.telegram_id == body.id:
        return MeResponse(
            telegram_id=user.telegram_id,
            auth_type=user.auth_type,
            email=user.email,
            is_email_verified=user.is_email_verified,
            pending_email=user.pending_email,
            name=user.name,
            username=user.username,
            language=user.language.value,
        )

    if user.telegram_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Already linked to a different Telegram account",
        )

    existing = await user_dao.get_by_telegram_id(body.id)
    if existing and existing.id != user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Telegram account already linked to another user",
        )

    user.telegram_id = body.id
    if body.username is not None:
        user.username = body.username

    async with uow:
        updated = await user_dao.update(user)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found during Telegram link",
            )
        await uow.commit()

    return MeResponse(
        telegram_id=updated.telegram_id,
        auth_type=updated.auth_type,
        email=updated.email,
        is_email_verified=updated.is_email_verified,
        pending_email=updated.pending_email,
        name=updated.name,
        username=updated.username,
        language=updated.language.value,
    )


@router.get("/me", response_model=MeResponse)
@inject
async def get_public_user_profile(
    user: CurrentUser,
) -> MeResponse:
    return MeResponse(
        telegram_id=user.telegram_id,
        auth_type=user.auth_type,
        email=user.email,
        is_email_verified=user.is_email_verified,
        pending_email=user.pending_email,
        name=user.name,
        username=user.username,
        language=user.language.value,
    )


@router.post("/change-password", response_model=ChangePasswordResponse)
@inject
async def change_public_user_password(
    body: ChangePasswordRequest,
    user: CurrentUser,
    config: FromDishka[AppConfig],
    uow: FromDishka[UnitOfWork],
    user_dao: FromDishka[UserDao],
) -> ChangePasswordResponse:
    key = config.crypt_key.get_secret_value()

    if not _verify_password(body.current_password, user.password_hash or "", key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is invalid",
        )

    if _verify_password(body.new_password, user.password_hash or "", key):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="New password must be different from current password",
        )

    user.password_hash = _hash_password(body.new_password, key)

    async with uow:
        updated = await user_dao.update(user)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found during password update",
            )
        await uow.commit()

    return ChangePasswordResponse(success=True)


@router.post("/email/change", response_model=ChangeEmailResponse)
@inject
async def change_email(
    body: ChangeEmailRequest,
    user: CurrentUser,
    uow: FromDishka[UnitOfWork],
    user_dao: FromDishka[UserDao],
) -> ChangeEmailResponse:
    existing = await user_dao.get_by_email(body.email)
    if existing and existing.id != user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already exists",
        )

    user.pending_email = body.email
    user.is_email_verified = False
    user.email_verification_code_hash = None
    user.email_verification_expires_at = None

    async with uow:
        updated = await user_dao.update(user)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found during email change",
            )
        await uow.commit()

    return ChangeEmailResponse(success=True, pending_email=body.email)


@router.post("/email/request-verification", response_model=RequestEmailVerificationCodeResponse)
@inject
async def request_email_verification_code(
    body: RequestEmailVerificationCodeRequest,
    user: CurrentUser,
    config: FromDishka[AppConfig],
    uow: FromDishka[UnitOfWork],
    user_dao: FromDishka[UserDao],
) -> RequestEmailVerificationCodeResponse:
    if not _is_email_delivery_enabled(config):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email delivery is not configured",
        )

    requested_email = body.email
    if requested_email and user.email and user.is_email_verified and requested_email != user.email:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email change is available only for users without verified email",
        )

    if requested_email and requested_email != user.email:
        existing = await user_dao.get_by_email(requested_email)
        if existing and existing.id != user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already exists",
            )
        user.pending_email = requested_email
        user.is_email_verified = False
    elif requested_email and requested_email == user.email and user.is_email_verified:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already verified",
        )

    target_email = user.pending_email or user.email
    if not target_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is required for verification",
        )

    code = _generate_email_verification_code()
    user.email_verification_code_hash = _hash_email_verification_code(
        code,
        config.crypt_key.get_secret_value(),
    )
    user.email_verification_expires_at = datetime_now() + timedelta(
        minutes=config.email.verification_code_ttl_minutes
    )

    async with uow:
        updated = await user_dao.update(user)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found during email verification request",
            )
        await uow.commit()

    await _send_email_verification_code(
        config=config,
        target_email=target_email,
        verification_code=code,
    )

    return RequestEmailVerificationCodeResponse(
        success=True,
        target_email=target_email,
        expires_at=user.email_verification_expires_at,
    )


@router.post("/email/confirm", response_model=ConfirmEmailVerificationResponse)
@inject
async def confirm_email_verification(
    body: ConfirmEmailVerificationRequest,
    user: CurrentUser,
    config: FromDishka[AppConfig],
    uow: FromDishka[UnitOfWork],
    user_dao: FromDishka[UserDao],
) -> ConfirmEmailVerificationResponse:
    if not user.email_verification_code_hash or not user.email_verification_expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email verification was not requested",
        )

    if user.email_verification_expires_at < datetime_now():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Verification code has expired",
        )

    incoming_hash = _hash_email_verification_code(body.code, config.crypt_key.get_secret_value())
    if not hmac.compare_digest(incoming_hash, user.email_verification_code_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code",
        )

    verified_email = user.pending_email or user.email
    if not verified_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No email to confirm",
        )

    if user.pending_email:
        existing = await user_dao.get_by_email(user.pending_email)
        if existing and existing.id != user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already exists",
            )
        user.email = user.pending_email

    user.pending_email = None
    user.is_email_verified = True
    user.email_verification_code_hash = None
    user.email_verification_expires_at = None

    async with uow:
        updated = await user_dao.update(user)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found during email confirmation",
            )
        await uow.commit()

    return ConfirmEmailVerificationResponse(
        success=True,
        email=verified_email,
    )
