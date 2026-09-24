"""Password hashing, user JWTs and device credentials."""

import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import get_settings
from app.core.timeutil import utcnow

_hasher = PasswordHasher()  # Argon2id with library defaults (RFC 9106 low-memory profile)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_access_token(
    user_id: uuid.UUID, facility_id: uuid.UUID, role: str, token_version: int
) -> str:
    s = get_settings()
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "fac": str(facility_id),
        "role": role,
        "ver": token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=s.access_token_minutes)).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, s.jwt_secret.get_secret_value(), algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    s = get_settings()
    payload = jwt.decode(
        token,
        s.jwt_secret.get_secret_value(),
        algorithms=[s.jwt_algorithm],
        options={"require": ["sub", "exp", "iat"]},
    )
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return payload


# --- Device credentials ----------------------------------------------------------------
# Device secrets are 256-bit random values, so a single SHA-256 is an adequate verifier
# (no dictionary attack is possible); a slow KDF would only add latency per request.


def new_device_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_device_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def device_secret_matches(secret: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_device_secret(secret), stored_hash)
