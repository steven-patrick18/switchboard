import base64
import hashlib
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import settings


def _prehash(password: str) -> bytes:
    # bcrypt silently caps input at 72 bytes. SHA-256 + base64 first so
    # passwords of any length are fully covered (the bcrypt_sha256 pattern).
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(subject: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.app_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str | None:
    try:
        payload = jwt.decode(
            token, settings.app_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError:
        return None
    return payload.get("sub")
