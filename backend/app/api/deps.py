import uuid
from collections.abc import Callable

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token, device_secret_matches
from app.database.session import get_db
from app.models import Device, User
from app.models.enums import UserRole, UserStatus

_bearer = HTTPBearer(auto_error=False)

_UNAUTH = HTTPException(
    status.HTTP_401_UNAUTHORIZED,
    "invalid or expired credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def user_from_token(db: Session, token: str) -> User:
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _UNAUTH from None
    user = db.get(User, user_id)
    if (
        user is None
        or user.status is not UserStatus.ACTIVE
        or payload.get("ver") != user.token_version
    ):
        raise _UNAUTH
    return user


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)
) -> User:
    if creds is None:
        raise _UNAUTH
    return user_from_token(db, creds.credentials)


def require_roles(*roles: UserRole) -> Callable[..., User]:
    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")
        return user

    return dep


ADMIN = require_roles(UserRole.ADMIN)
MANAGER = require_roles(UserRole.ADMIN, UserRole.SUPERVISOR)
CLINICAL = require_roles(UserRole.ADMIN, UserRole.SUPERVISOR, UserRole.NURSE)
ANY_STAFF = require_roles(*UserRole)


def current_device(
    authorization: str | None = Header(default=None), db: Session = Depends(get_db)
) -> Device:
    """Device token format: 'Bearer <device_uuid>.<secret>' over TLS."""
    if not authorization or not authorization.startswith("Bearer "):
        raise _UNAUTH
    token = authorization.removeprefix("Bearer ")
    dev_id, _, secret = token.partition(".")
    try:
        device = db.get(Device, uuid.UUID(dev_id))
    except ValueError:
        raise _UNAUTH from None
    if device is None or not secret or not device_secret_matches(secret, device.credential_hash):
        raise _UNAUTH
    return device


def scoped(obj, user: User, name: str = "resource"):
    """404 (not 403) for other tenants' objects: do not leak existence across facilities."""
    if obj is None or getattr(obj, "facility_id", None) != user.facility_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{name} not found")
    return obj
