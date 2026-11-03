from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.core.config import APP_NAME, JWT_SECRET_KEY

# Durasi token
ACCESS_TOKEN_EXPIRE_HOURS = 24

JWT_ALGORITHM = "HS256"

# bcrypt maksimal 72 byte password
_MAX_SECRET_BYTES = 72


def _normalize(secret: str) -> bytes:
    return secret.encode("utf-8")[:_MAX_SECRET_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_normalize(password), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_normalize(plain), hashed.encode())
    except (ValueError, TypeError):
        return False


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        hours=ACCESS_TOKEN_EXPIRE_HOURS
    )
    to_encode.update({"exp": expire, "iss": APP_NAME})
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Kembalikan payload jika valid, None jika tidak."""
    try:
        return jwt.decode(
            token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM]
        )
    except JWTError:
        return None
