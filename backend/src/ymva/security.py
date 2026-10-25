"""Password hashing, opaque token hashing, and encryption of stored credentials."""

import hashlib
import hmac
import json
import secrets
from functools import lru_cache
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

from ymva.config import get_settings

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_token(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Deterministic keyed hash, so only the hash of a session/API token is stored."""
    key = get_settings().secret_key.get_secret_value().encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


class EncryptionUnavailableError(RuntimeError):
    pass


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().encryption_key.get_secret_value()
    if not key:
        raise EncryptionUnavailableError("ENCRYPTION_KEY is not configured")
    return Fernet(key.encode())


def encrypt_json(data: dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str) -> dict[str, Any]:
    try:
        value: dict[str, Any] = json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as exc:
        raise EncryptionUnavailableError("Stored credential could not be decrypted") from exc
    return value
