import base64
import os
import tempfile
import uuid

import pytest

# Configure before any app import. Test-only values; real deployments inject secrets.
os.environ.setdefault("VISIONAID_JWT_SECRET", "test-only-secret-" + uuid.uuid4().hex)
os.environ["VISIONAID_DATABASE_URL"] = os.environ.get(
    "VISIONAID_TEST_DATABASE_URL",
    "postgresql+psycopg://visionaid:visionaid@localhost:5432/visionaid_test",
)
os.environ["VISIONAID_RUN_WORKERS_IN_PROCESS"] = "false"
os.environ["VISIONAID_EVIDENCE_KEY"] = base64.urlsafe_b64encode(os.urandom(32)).decode()
os.environ["VISIONAID_EVIDENCE_DIR"] = tempfile.mkdtemp(prefix="visionaid-evidence-")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.api.routes.auth import login_limiter  # noqa: E402
from app.core.security import hash_device_secret, hash_password  # noqa: E402
from app.database.base import Base  # noqa: E402
from app.database.session import get_engine, session_factory  # noqa: E402
from app.main import app  # noqa: E402
from app.models import CareAssignment, Device, Facility, User  # noqa: E402
from app.models.enums import CareTier, UserRole  # noqa: E402

PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="session", autouse=True)
def schema():
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def clean():
    yield
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    login_limiter.reset()


@pytest.fixture
def db():
    s = session_factory()()
    yield s
    s.close()


class World:
    """A facility with a care network and one camera in Room 101 / Floor 1."""

    def __init__(self, db):
        self.facility = Facility(name="Test Home", timezone="Asia/Kolkata")
        db.add(self.facility)
        db.flush()
        fid = self.facility.id

        def user(name, role):
            u = User(
                facility_id=fid,
                name=name,
                email=f"{name}@example.org",
                role=role,
                password_hash=hash_password(PASSWORD),
            )
            db.add(u)
            return u

        self.admin = user("admin", UserRole.ADMIN)
        self.supervisor = user("supervisor", UserRole.SUPERVISOR)
        self.nurse = user("nurse", UserRole.NURSE)
        self.primary = user("primary", UserRole.CAREGIVER)
        self.secondary = user("secondary", UserRole.CAREGIVER)
        self.floor2 = user("floor2", UserRole.CAREGIVER)
        db.flush()
        for u, tier, floor, room in [
            (self.primary, CareTier.PRIMARY, "1", "101"),
            (self.secondary, CareTier.SECONDARY, "1", None),
            (self.floor2, CareTier.PRIMARY, "2", None),
            (self.supervisor, CareTier.SUPERVISOR, None, None),
        ]:
            db.add(CareAssignment(facility_id=fid, user_id=u.id, tier=tier, floor=floor, room=room))
        self.device_secret = "s3cret-" + uuid.uuid4().hex
        self.device = Device(
            facility_id=fid,
            name="Cam 101",
            room="101",
            floor="1",
            location="Wing A",
            credential_hash=hash_device_secret(self.device_secret),
        )
        db.add(self.device)
        db.commit()

    @property
    def device_headers(self):
        return {"Authorization": f"Bearer {self.device.id}.{self.device_secret}"}


@pytest.fixture
def world(db):
    return World(db)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def login(client, email: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
