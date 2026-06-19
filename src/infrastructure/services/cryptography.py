import dataclasses
import hashlib
import secrets
import string
from typing import Any, Awaitable, Callable, Final, Optional

from cryptography.fernet import Fernet
from loguru import logger
from pydantic import SecretStr

from src.application.common import Cryptographer
from src.core.config import AppConfig
from src.core.constants import ENCRYPTED_PREFIX
from src.infrastructure.common import json


class CryptographerImpl(Cryptographer):
    _ALPHABET: Final[str] = string.ascii_letters + string.digits

    def __init__(self, config: AppConfig):
        self.fernet = Fernet(config.crypt_key.get_secret_value().encode())
        self.config = config
        logger.info("Cryptographer initialized")

    def encrypt(self, data: str) -> str:
        encrypted = ENCRYPTED_PREFIX + self.fernet.encrypt(data.encode()).decode()
        logger.debug(f"Data encrypted with prefix '{ENCRYPTED_PREFIX}'")
        return encrypted

    def encrypt_recursive(self, value: Any) -> Any:
        if isinstance(value, SecretStr):
            return self.encrypt(value.get_secret_value())
        if isinstance(value, list):
            return [self.encrypt_recursive(v) for v in value]
        if isinstance(value, dict):
            return {k: self.encrypt_recursive(v) for k, v in value.items()}
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            encrypted_fields = {
                f.name: self.encrypt_recursive(getattr(value, f.name))
                for f in dataclasses.fields(value)
                if f.init
            }
            return value.__class__(**encrypted_fields)
        return value

    def decrypt(self, data: str) -> str:
        try:
            decrypted = self.fernet.decrypt(data.removeprefix(ENCRYPTED_PREFIX).encode()).decode()
            return decrypted
        except Exception as e:
            logger.error(f"Failed to decrypt data due to error '{e}'")
            raise

    def decrypt_recursive(self, value: Any) -> Any:
        if isinstance(value, str):
            if self.is_encrypted(value):
                try:
                    decrypted = self.decrypt(value)
                    return SecretStr(decrypted)
                except Exception:
                    logger.warning(f"Could not decrypt value starting with '{value[:10]}'")
                    return value
            return value

        if isinstance(value, list):
            return [self.decrypt_recursive(v) for v in value]

        if isinstance(value, dict):
            return {k: self.decrypt_recursive(v) for k, v in value.items()}

        return value

    def get_hash(self, data: Any) -> str:
        hashed_data = hashlib.sha256(json.bytes_encode(data)).hexdigest()
        logger.debug("Generated sha256 hash for data")
        return hashed_data

    def is_encrypted(self, value: str) -> bool:
        return isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX)

    def generate_random_code(self, length: int = 6) -> str:
        result = "".join(secrets.choice(self._ALPHABET) for _ in range(length))
        logger.debug(f"Random code of length {length} generated")
        return result

    async def generate_unique_code(
        self,
        is_taken: Callable[[str], Awaitable[Optional[Any]]],
        length: int = 6,
    ) -> str:
        while True:
            code = self.generate_random_code(length)
            if not await is_taken(code):
                return code
