"""Operator bootstrap. There is deliberately no self-service sign-up endpoint.

    python -m app.cli create-facility --name "Sunrise Home" --timezone Asia/Kolkata \
        --admin-email admin@example.org --admin-name "Admin"
The admin password is read interactively (never from argv, which lands in shell history).
"""

import argparse
import getpass
import sys
from zoneinfo import ZoneInfo

from app.core.security import hash_password
from app.database.session import session_factory
from app.models import Facility, User
from app.models.enums import UserRole
from app.services import audit


def create_facility(args) -> None:
    ZoneInfo(args.timezone)  # validate
    pw = getpass.getpass("Admin password (min 12 chars): ")
    if len(pw) < 12 or pw != getpass.getpass("Repeat: "):
        sys.exit("password too short or mismatch")
    with session_factory()() as db:
        fac = Facility(name=args.name, address=args.address, timezone=args.timezone)
        db.add(fac)
        db.flush()
        admin = User(
            facility_id=fac.id,
            name=args.admin_name,
            email=args.admin_email.lower(),
            role=UserRole.ADMIN,
            password_hash=hash_password(pw),
        )
        db.add(admin)
        db.flush()
        audit.record(db, "facility.created", facility_id=fac.id, user_id=admin.id)
        db.commit()
        print(f"facility {fac.id} created; admin {admin.id}")


def main() -> None:
    p = argparse.ArgumentParser(prog="visionaid")
    sub = p.add_subparsers(required=True)
    c = sub.add_parser("create-facility")
    c.add_argument("--name", required=True)
    c.add_argument("--address")
    c.add_argument("--timezone", default="UTC")
    c.add_argument("--admin-email", required=True)
    c.add_argument("--admin-name", required=True)
    c.set_defaults(fn=create_facility)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
