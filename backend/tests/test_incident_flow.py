"""End-to-end incident workflow through the HTTP API and the periodic workers."""

import uuid
from datetime import timedelta

from sqlalchemy import select

from app.core.timeutil import utcnow
from app.models import Incident, Notification
from app.models.enums import IncidentStatus as S
from app.services import incidents
from tests.conftest import login


def trigger(
    client, world, score=0.9, descent=True, immobility=12.0, recovered=False, event_id=None
):
    return client.post(
        "/api/v1/device/events/fall",
        headers=world.device_headers,
        json={
            "event_id": event_id or uuid.uuid4().hex,
            "occurred_at": utcnow().isoformat(),
            "trigger_score": score,
            "descent_detected": descent,
            "immobility_seconds": immobility,
            "recovered": recovered,
        },
    )


def recipients(db, incident_id):
    return {
        n.recipient_id
        for n in db.scalars(select(Notification).where(Notification.incident_id == incident_id))
    }


def test_device_auth_required(client, world):
    r = client.post("/api/v1/device/heartbeat", headers={"Authorization": "Bearer nope"}, json={})
    assert r.status_code == 401
    bad = {"Authorization": f"Bearer {world.device.id}.wrong-secret"}
    assert trigger(client, type("W", (), {"device_headers": bad})()).status_code == 401


def test_confirmed_fall_alerts_primary_only(client, world, db):
    r = trigger(client, world)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] and body["status"] == "ALERT_SENT"
    assert body["upload_pre_event_seconds"] == 30
    assert recipients(db, uuid.UUID(body["incident_id"])) == {world.primary.id}


def test_trigger_is_idempotent_and_retriggers_group(client, world, db):
    first = trigger(client, world, score=0.5, immobility=0, event_id="evt-00000001").json()
    again = trigger(client, world, score=0.5, immobility=0, event_id="evt-00000001").json()
    assert again["incident_id"] == first["incident_id"] and not again["created"]
    grouped = trigger(client, world, score=0.7, immobility=0, event_id="evt-00000002").json()
    assert grouped["incident_id"] == first["incident_id"]
    inc = db.get(Incident, uuid.UUID(first["incident_id"]))
    assert inc.trigger_count == 2
    assert inc.status is S.POTENTIAL_FALL


def test_observation_promotes_to_alert(client, world, db):
    inc_id = trigger(client, world, score=0.8, immobility=0).json()["incident_id"]
    assert db.get(Incident, uuid.UUID(inc_id)).status is S.POTENTIAL_FALL
    r = client.post(
        f"/api/v1/device/incidents/{inc_id}/observations",
        headers=world.device_headers,
        json={"immobility_seconds": 15},
    )
    assert r.json()["status"] == "ALERT_SENT"


def test_caregiver_workflow_stops_escalation(client, world, db):
    inc_id = trigger(client, world).json()["incident_id"]
    h = login(client, "primary@example.org")
    r = client.post(f"/api/v1/incidents/{inc_id}/acknowledge", headers=h, json={})
    assert r.status_code == 200 and r.json()["status"] == "ACKNOWLEDGED"
    assert r.json()["next_escalation_at"] is None
    assert (
        client.post(f"/api/v1/incidents/{inc_id}/respond", headers=h).json()["status"]
        == "RESPONDING"
    )
    r = client.post(
        f"/api/v1/incidents/{inc_id}/resolve",
        headers=h,
        json={"note": "Resident assisted, no injury observed"},
    )
    assert r.json()["status"] == "RESOLVED" and r.json()["resolution_seconds"] is not None
    # Closed incidents cannot be reopened by a stray tap.
    assert client.post(f"/api/v1/incidents/{inc_id}/acknowledge", headers=h).status_code == 409
    detail = client.get(f"/api/v1/incidents/{inc_id}", headers=h).json()
    kinds = [(e["kind"], e["to_status"]) for e in detail["timeline"]]
    assert ("transition", "RESOLVED") in kinds and ("escalation", None) in kinds


def test_escalation_ladder_then_unresolved_still_actionable(client, world, db):
    inc_id = uuid.UUID(trigger(client, world).json()["incident_id"])
    inc = db.get(Incident, inc_id)
    t0 = inc.alert_sent_at

    def cycle(seconds):
        db.expire_all()
        incidents.run_escalation_cycle(db, t0 + timedelta(seconds=seconds))
        db.commit()
        return db.get(Incident, inc_id)

    assert cycle(10).status is S.ALERT_SENT  # not yet due
    inc = cycle(31)
    assert inc.status is S.ESCALATED and inc.escalation_level == 1
    assert world.secondary.id in recipients(db, inc_id)
    inc = cycle(61)
    assert inc.escalation_level == 2 and world.supervisor.id in recipients(db, inc_id)
    inc = cycle(121)  # EMERGENCY tier unassigned → falls back to supervisors/admins
    assert inc.escalation_level == 3 and world.admin.id in recipients(db, inc_id)
    inc = cycle(301)
    assert inc.status is S.UNRESOLVED and inc.next_escalation_at is None
    assert world.floor2.id not in recipients(db, inc_id)  # other floor never paged

    h = login(client, "secondary@example.org")
    r = client.post(f"/api/v1/incidents/{inc_id}/acknowledge", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "ACKNOWLEDGED"


def test_potential_fall_without_recovery_is_promoted(client, world, db):
    inc_id = uuid.UUID(trigger(client, world, score=0.8, immobility=0).json()["incident_id"])
    incidents.run_expiry_cycle(db, utcnow() + timedelta(seconds=46))
    db.commit()
    assert db.get(Incident, inc_id).status is S.ALERT_SENT


def test_recovered_potential_fall_and_stale_suspicious_are_cancelled(client, world, db):
    pot = uuid.UUID(
        trigger(client, world, score=0.8, immobility=0, recovered=True).json()["incident_id"]
    )
    incidents.run_expiry_cycle(db, utcnow() + timedelta(seconds=200))
    db.commit()
    assert db.get(Incident, pot).status is S.CANCELLED
    sus = uuid.UUID(trigger(client, world, score=0.4).json()["incident_id"])
    assert db.get(Incident, sus).status is S.SUSPICIOUS
    incidents.run_expiry_cycle(db, utcnow() + timedelta(seconds=200))
    db.commit()
    db.expire_all()
    assert db.get(Incident, sus).status is S.CANCELLED


def test_late_low_verifier_score_never_cancels_raised_alert(client, world, db):
    inc_id = uuid.UUID(trigger(client, world).json()["incident_id"])
    inc = db.get(Incident, inc_id)
    incidents.observe(db, inc, incidents.ObservationInput(verifier_score=0.01), actor="verifier")
    db.commit()
    assert db.get(Incident, inc_id).status is S.ALERT_SENT


def test_user_cannot_reach_system_only_states(client, world):
    inc_id = trigger(client, world).json()["incident_id"]
    h = login(client, "primary@example.org")
    assert client.post(f"/api/v1/incidents/{inc_id}/cancel", headers=h).status_code == 404
    assert client.post(f"/api/v1/incidents/{inc_id}/escalate", headers=h).status_code == 404


def test_home_screen_is_not_all_clear_until_incident_resolved(client, world):
    inc_id = trigger(client, world).json()["incident_id"]
    client.post(
        "/api/v1/device/heartbeat",
        headers=world.device_headers,
        json={
            "timestamp": utcnow().isoformat(),
            "firmware_version": "t",
            "model_version": "t",
            "uptime_seconds": 1,
        },
    )
    h = login(client, "primary@example.org")

    def overview():
        return client.get("/api/v1/facility/overview", headers=h).json()

    assert overview()["critical_incident_count"] == 1 and not overview()["all_clear"]
    client.post(f"/api/v1/incidents/{inc_id}/acknowledge", headers=h)
    ov = overview()
    assert ov["critical_incident_count"] == 0
    assert ov["all_clear"] is False  # acknowledged ≠ safe: someone is still on the floor
    client.post(f"/api/v1/incidents/{inc_id}/resolve", headers=h)
    assert overview()["all_clear"] is True
