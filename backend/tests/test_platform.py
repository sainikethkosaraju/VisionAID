"""Auth/RBAC/tenancy, device health, evidence privacy, analytics."""

import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.timeutil import utcnow
from app.models import AuditLog, Device, EvidenceClip, Facility, Incident, Notification, User
from app.models.enums import DeviceStatus, UserRole
from app.models.enums import IncidentStatus as S
from app.services import analytics, devices, evidence
from tests.conftest import PASSWORD, login
from tests.test_incident_flow import trigger

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"


def heartbeat(client, world, **over):
    body = {
        "timestamp": utcnow().isoformat(),
        "firmware_version": "0.1.0",
        "model_version": "trigger-v0",
        "uptime_seconds": 42,
        "rssi_dbm": -61,
        "buffer_effective_seconds": 60.0,
    } | over
    return client.post("/api/v1/device/heartbeat", headers=world.device_headers, json=body)


# --- Auth & tenancy ------------------------------------------------------------------------
def test_login_failures_are_uniform_and_rate_limited(client, world):
    bad = client.post(
        "/api/v1/auth/login", json={"email": "primary@example.org", "password": "wrong"}
    )
    unknown = client.post(
        "/api/v1/auth/login", json={"email": "ghost@example.org", "password": "wrong"}
    )
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json()
    codes = [
        client.post(
            "/api/v1/auth/login", json={"email": "x@example.org", "password": "y"}
        ).status_code
        for _ in range(12)
    ]
    assert 429 in codes


def test_disabling_user_revokes_existing_tokens(client, world):
    h = login(client, "primary@example.org")
    assert client.get("/api/v1/auth/me", headers=h).status_code == 200
    admin = login(client, "admin@example.org")
    r = client.patch(
        f"/api/v1/users/{world.primary.id}", headers=admin, json={"status": "DISABLED"}
    )
    assert r.status_code == 200
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401


def test_rbac(client, world):
    cg = login(client, "primary@example.org")
    assert client.post("/api/v1/devices", headers=cg, json={"name": "x"}).status_code == 403
    assert client.get("/api/v1/analytics/incidents", headers=cg).status_code == 403
    assert (
        client.post(
            "/api/v1/users",
            headers=cg,
            json={
                "name": "n",
                "email": "n@example.org",
                "role": "ADMIN",
                "password": "long-enough-pass",
            },
        ).status_code
        == 403
    )


def test_other_facility_objects_are_invisible(client, world, db):
    other = Facility(name="Other", timezone="UTC")
    db.add(other)
    db.flush()
    from app.core.security import hash_password

    db.add(
        User(
            facility_id=other.id,
            name="o",
            email="o@example.org",
            role=UserRole.ADMIN,
            password_hash=hash_password(PASSWORD),
        )
    )
    db.commit()
    inc_id = trigger(client, world).json()["incident_id"]
    h = login(client, "o@example.org")
    assert client.get(f"/api/v1/incidents/{inc_id}", headers=h).status_code == 404
    assert client.get(f"/api/v1/devices/{world.device.id}", headers=h).status_code == 404
    assert client.post(f"/api/v1/incidents/{inc_id}/acknowledge", headers=h).status_code == 404
    assert client.get("/api/v1/incidents", headers=h).json() == []


def test_device_registration_token_is_usable_once_shown(client, world):
    admin = login(client, "admin@example.org")
    r = client.post(
        "/api/v1/devices", headers=admin, json={"name": "Cam 202", "room": "202", "floor": "2"}
    )
    assert r.status_code == 201
    token = r.json()["device_token"]
    assert r.json()["device"]["status"] == "UNPROVISIONED"
    hb = client.post(
        "/api/v1/device/heartbeat",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "timestamp": utcnow().isoformat(),
            "firmware_version": "0.1.0",
            "model_version": "m",
            "uptime_seconds": 1,
        },
    )
    assert hb.status_code == 200
    dev_id = r.json()["device"]["id"]
    rot = client.post(f"/api/v1/devices/{dev_id}/rotate-credentials", headers=admin)
    assert rot.status_code == 200
    old = client.post(
        "/api/v1/device/heartbeat",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "timestamp": utcnow().isoformat(),
            "firmware_version": "0.1.0",
            "model_version": "m",
            "uptime_seconds": 2,
        },
    )
    assert old.status_code == 401


# --- Device health -----------------------------------------------------------------------
def test_heartbeat_config_push_and_commands(client, world):
    admin = login(client, "admin@example.org")
    client.post(
        f"/api/v1/devices/{world.device.id}/commands", headers=admin, json={"command": "self_test"}
    )
    r = heartbeat(client, world)
    assert r.status_code == 200
    assert r.json()["config"]["buffer_seconds"] == get_settings().buffer_seconds
    assert [c["command"] for c in r.json()["commands"]] == ["self_test"]
    assert heartbeat(client, world).json()["commands"] == []  # delivered once


def test_small_buffer_and_faults_degrade(client, world, db):
    heartbeat(client, world, buffer_effective_seconds=12.0)
    db.expire_all()
    assert db.get(Device, world.device.id).status is DeviceStatus.DEGRADED
    heartbeat(client, world)
    db.expire_all()
    assert db.get(Device, world.device.id).status is DeviceStatus.ONLINE
    heartbeat(client, world, faults=["camera_init_failed"])
    db.expire_all()
    assert db.get(Device, world.device.id).status is DeviceStatus.DEGRADED


def test_silent_device_goes_offline_and_supervisor_is_told(client, world, db):
    heartbeat(client, world)
    now = utcnow()
    devices.sweep_health(db, now + timedelta(seconds=65))
    db.commit()
    assert db.get(Device, world.device.id).status is DeviceStatus.DEGRADED
    devices.sweep_health(db, now + timedelta(seconds=125))
    db.commit()
    assert db.get(Device, world.device.id).status is DeviceStatus.OFFLINE
    notes = db.scalars(select(Notification).where(Notification.kind == "device")).all()
    assert any("OFFLINE" in n.title and n.recipient_id == world.supervisor.id for n in notes)
    h = login(client, "primary@example.org")
    ov = client.get("/api/v1/facility/overview", headers=h).json()
    assert ov["all_clear"] is False and ov["devices"]["offline"] == 1


# --- Evidence privacy ------------------------------------------------------------------------
def upload(client, world, inc_id, n=3):
    base = int(utcnow().timestamp() * 1000)
    files = [("frames", (f"{base + i * 200}.jpg", JPEG, "image/jpeg")) for i in range(n)]
    return client.post(
        f"/api/v1/device/incidents/{inc_id}/evidence?segment=pre",
        headers=world.device_headers,
        files=files,
    )


def test_evidence_is_encrypted_access_controlled_and_audited(client, world, db):
    inc_id = trigger(client, world).json()["incident_id"]
    r = upload(client, world, inc_id)
    assert r.status_code == 201, r.text
    assert r.json()["verifier_score"] is None  # PENDING INTEGRATION: no fake score
    clip = db.scalar(select(EvidenceClip))
    raw = (Path(get_settings().evidence_dir) / clip.storage_key).read_bytes()
    assert b"\xff\xd8\xff\xe0" not in raw  # no plaintext JPEG on disk

    cg = login(client, "primary@example.org")
    assert client.get(f"/api/v1/evidence/{clip.id}/frames/0", headers=cg).status_code == 403
    nurse = login(client, "nurse@example.org")
    r = client.get(f"/api/v1/evidence/{clip.id}/frames/1", headers=nurse)
    assert r.status_code == 200 and r.content == JPEG
    assert r.headers["cache-control"] == "no-store"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "evidence.viewed"))

    evidence.purge_expired(db, utcnow() + timedelta(days=31))
    db.commit()
    assert client.get(f"/api/v1/evidence/{clip.id}/frames/0", headers=nurse).status_code == 410


def test_evidence_rejects_non_jpeg_and_foreign_incident(client, world, db):
    inc_id = trigger(client, world).json()["incident_id"]
    bad = client.post(
        f"/api/v1/device/incidents/{inc_id}/evidence?segment=pre",
        headers=world.device_headers,
        files=[("frames", ("1.jpg", b"not a jpeg", "image/jpeg"))],
    )
    assert bad.status_code == 422
    assert upload(client, world, uuid.uuid4()).status_code == 404


# --- Analytics -----------------------------------------------------------------------------
def test_analytics_are_computed_from_records(client, world, db):
    a = trigger(client, world).json()["incident_id"]
    b = trigger(client, world, event_id="later-event-1").json()["incident_id"]
    assert a == b  # grouped: one incident
    h = login(client, "primary@example.org")
    client.post(f"/api/v1/incidents/{a}/acknowledge", headers=h)
    client.post(
        f"/api/v1/incidents/{a}/false_positive",
        headers=h,
        json={"note": "resident sat down on floor deliberately"},
    )
    heartbeat(client, world)
    m = login(client, "supervisor@example.org")
    stats = client.get("/api/v1/analytics/incidents?days=1", headers=m).json()
    assert stats["counts"]["alerts"] == 1
    assert stats["counts"]["false_positives"] == 1
    assert stats["false_positive_rate"] == 1.0
    assert stats["avg_acknowledge_seconds"] is not None
    assert stats["by_room"] == {"101": 1}
    inc = db.get(Incident, uuid.UUID(a))
    assert inc.status is S.FALSE_POSITIVE

    uptime = analytics.device_uptime(
        db, world.facility.id, utcnow() - timedelta(hours=1), utcnow() + timedelta(minutes=1)
    )
    assert 0 < uptime[0]["uptime_ratio"] < 1
