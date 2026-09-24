"""Users, care assignments (the care network) and residents."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import ADMIN, ANY_STAFF, MANAGER, scoped
from app.core.security import hash_password
from app.database.session import get_db
from app.models import CareAssignment, Resident, User
from app.models.enums import ResidentStatus, UserStatus
from app.schemas import (
    AssignmentCreate,
    AssignmentOut,
    ResidentCreate,
    ResidentOut,
    ResidentUpdate,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services import audit

router = APIRouter(tags=["people"])


@router.get("/users", response_model=list[UserOut])
def list_users(user: User = Depends(MANAGER), db: Session = Depends(get_db)):
    return db.scalars(
        select(User).where(User.facility_id == user.facility_id).order_by(User.name)
    ).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, admin: User = Depends(ADMIN), db: Session = Depends(get_db)):
    if db.scalar(select(User.id).where(func.lower(User.email) == body.email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    u = User(
        facility_id=admin.facility_id,
        name=body.name,
        email=body.email.lower(),
        phone=body.phone,
        role=body.role,
        password_hash=hash_password(body.password),
    )
    db.add(u)
    db.flush()
    audit.record(
        db,
        "user.created",
        facility_id=admin.facility_id,
        user_id=admin.id,
        target_type="user",
        target_id=u.id,
        details={"role": body.role.value},
    )
    db.commit()
    return u


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    admin: User = Depends(ADMIN),
    db: Session = Depends(get_db),
):
    u = scoped(db.get(User, user_id), admin, "user")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(u, k, v)
    if "role" in changes or changes.get("status") is UserStatus.DISABLED:
        u.token_version += 1  # revoke existing sessions
    audit.record(
        db,
        "user.updated",
        facility_id=admin.facility_id,
        user_id=admin.id,
        target_type="user",
        target_id=u.id,
        details={k: str(v) for k, v in changes.items()},
    )
    db.commit()
    return u


@router.patch("/me/duty", response_model=UserOut)
def set_duty(on_duty: bool, user: User = Depends(ANY_STAFF), db: Session = Depends(get_db)):
    user.on_duty = on_duty
    audit.record(
        db, "user.duty", facility_id=user.facility_id, user_id=user.id, details={"on_duty": on_duty}
    )
    db.commit()
    return user


@router.get("/care-assignments", response_model=list[AssignmentOut])
def list_assignments(user: User = Depends(ANY_STAFF), db: Session = Depends(get_db)):
    return db.scalars(
        select(CareAssignment).where(
            CareAssignment.facility_id == user.facility_id, CareAssignment.active.is_(True)
        )
    ).all()


@router.post("/care-assignments", response_model=AssignmentOut, status_code=201)
def create_assignment(
    body: AssignmentCreate, mgr: User = Depends(MANAGER), db: Session = Depends(get_db)
):
    scoped(db.get(User, body.user_id), mgr, "user")
    a = CareAssignment(facility_id=mgr.facility_id, **body.model_dump())
    db.add(a)
    db.flush()
    audit.record(
        db,
        "care_assignment.created",
        facility_id=mgr.facility_id,
        user_id=mgr.id,
        target_type="care_assignment",
        target_id=a.id,
    )
    db.commit()
    return a


@router.delete("/care-assignments/{assignment_id}", status_code=204)
def deactivate_assignment(
    assignment_id: uuid.UUID, mgr: User = Depends(MANAGER), db: Session = Depends(get_db)
):
    a = scoped(db.get(CareAssignment, assignment_id), mgr, "assignment")
    a.active = False
    audit.record(
        db,
        "care_assignment.deactivated",
        facility_id=mgr.facility_id,
        user_id=mgr.id,
        target_type="care_assignment",
        target_id=a.id,
    )
    db.commit()


@router.get("/residents", response_model=list[ResidentOut])
def list_residents(
    include_discharged: bool = False, user: User = Depends(ANY_STAFF), db: Session = Depends(get_db)
):
    q = select(Resident).where(Resident.facility_id == user.facility_id)
    if not include_discharged:
        q = q.where(Resident.status == ResidentStatus.ACTIVE)
    return db.scalars(q.order_by(Resident.floor, Resident.room, Resident.name)).all()


@router.post("/residents", response_model=ResidentOut, status_code=201)
def create_resident(
    body: ResidentCreate, mgr: User = Depends(MANAGER), db: Session = Depends(get_db)
):
    r = Resident(facility_id=mgr.facility_id, **body.model_dump())
    db.add(r)
    db.flush()
    audit.record(
        db,
        "resident.created",
        facility_id=mgr.facility_id,
        user_id=mgr.id,
        target_type="resident",
        target_id=r.id,
    )
    db.commit()
    return r


@router.patch("/residents/{resident_id}", response_model=ResidentOut)
def update_resident(
    resident_id: uuid.UUID,
    body: ResidentUpdate,
    mgr: User = Depends(MANAGER),
    db: Session = Depends(get_db),
):
    r = scoped(db.get(Resident, resident_id), mgr, "resident")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(r, k, v)
    audit.record(
        db,
        "resident.updated",
        facility_id=mgr.facility_id,
        user_id=mgr.id,
        target_type="resident",
        target_id=r.id,
    )
    db.commit()
    return r
