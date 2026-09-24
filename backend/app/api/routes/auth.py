from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.config import get_settings
from app.core.rate_limit import RateLimiter
from app.core.security import create_access_token, hash_password, verify_password
from app.database.session import get_db
from app.models import User
from app.models.enums import UserStatus
from app.schemas import LoginIn, TokenOut, UserOut
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])
login_limiter = RateLimiter(get_settings().login_rate_limit_per_minute)

# Verified against when the email is unknown, so response time does not reveal accounts.
_DUMMY_HASH = hash_password("visionaid-timing-equaliser")


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> TokenOut:
    ip = request.client.host if request.client else "unknown"
    if not login_limiter.allow(f"{ip}:{body.email.lower()}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many login attempts")
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or user.status is not UserStatus.ACTIVE:
        audit.record(
            db,
            "auth.login_failed",
            facility_id=user.facility_id if user else None,
            details={"email": body.email.lower()},
            ip=ip,
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
    audit.record(db, "auth.login", facility_id=user.facility_id, user_id=user.id, ip=ip)
    db.commit()
    s = get_settings()
    return TokenOut(
        access_token=create_access_token(
            user.id, user.facility_id, user.role.value, user.token_version
        ),
        expires_in=s.access_token_minutes * 60,
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user
