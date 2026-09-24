"""Device gateway: the only endpoints a camera can call. Authenticated per device."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import current_device
from app.core.config import get_settings
from app.core.timeutil import utcnow
from app.database.session import get_db
from app.models import Device, Incident
from app.models.enums import DeviceStatus
from app.schemas import FallTriggerIn, HeartbeatIn, HeartbeatOut, ObservationIn, TriggerAck
from app.services import audit, devices, evidence, incidents
from app.services.verifier import get_verifier

router = APIRouter(prefix="/device", tags=["device-gateway"])

MAX_CLOCK_SKEW_S = 300


def _server_time(device_time: datetime) -> datetime:
    """Trust device timestamps only within skew bounds (no RTC; clock comes from SNTP)."""
    now = utcnow()
    if device_time.tzinfo is None:
        device_time = device_time.replace(tzinfo=UTC)
    return device_time if abs((now - device_time).total_seconds()) <= MAX_CLOCK_SKEW_S else now


def _own_incident(db: Session, device: Device, incident_id: uuid.UUID) -> Incident:
    inc = db.get(Incident, incident_id)
    if inc is None or inc.device_id != device.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    return inc


@router.post("/heartbeat", response_model=HeartbeatOut)
def heartbeat(
    body: HeartbeatIn, device: Device = Depends(current_device), db: Session = Depends(get_db)
) -> HeartbeatOut:
    was_unprovisioned = device.status is DeviceStatus.UNPROVISIONED
    devices.record_heartbeat(
        db,
        device,
        devices.Heartbeat(
            firmware_version=body.firmware_version,
            model_version=body.model_version,
            uptime_seconds=body.uptime_seconds,
            rssi_dbm=body.rssi_dbm,
            temperature_c=body.temperature_c,
            power_state=body.power_state,
            free_psram_bytes=body.free_psram_bytes,
            buffer_effective_seconds=body.buffer_effective_seconds,
            faults=body.faults,
        ),
    )
    if was_unprovisioned:
        audit.record(
            db, "device.first_contact", facility_id=device.facility_id, device_id=device.id
        )
    commands, device.pending_commands = device.pending_commands, []
    db.commit()
    return HeartbeatOut(
        server_time=utcnow(), config=devices.device_config(device), commands=commands
    )


@router.post("/events/fall", response_model=TriggerAck)
def fall_trigger(
    body: FallTriggerIn, device: Device = Depends(current_device), db: Session = Depends(get_db)
) -> TriggerAck:
    inc, created = incidents.ingest_trigger(
        db,
        device,
        incidents.TriggerInput(
            event_id=body.event_id,
            occurred_at=_server_time(body.occurred_at),
            trigger_score=body.trigger_score,
            descent_detected=body.descent_detected,
            immobility_seconds=body.immobility_seconds,
            recovered=body.recovered,
            features=body.features,
        ),
    )
    db.commit()
    s = get_settings()
    return TriggerAck(
        incident_id=inc.id,
        status=inc.status,
        created=created,
        upload_pre_event_seconds=s.pre_event_seconds,
        upload_post_event_seconds=s.post_event_seconds,
        evidence_accepted=s.evidence_key is not None,
    )


@router.post("/incidents/{incident_id}/observations")
def observation(
    incident_id: uuid.UUID,
    body: ObservationIn,
    device: Device = Depends(current_device),
    db: Session = Depends(get_db),
):
    inc = _own_incident(db, device, incident_id)
    incidents.observe(db, inc, incidents.ObservationInput(**body.model_dump()))
    db.commit()
    return {"incident_id": inc.id, "status": inc.status}


@router.post("/incidents/{incident_id}/evidence", status_code=201)
async def upload_evidence(
    incident_id: uuid.UUID,
    segment: str = Query(pattern="^(pre|post)$"),
    frames: list[UploadFile] = File(...),
    device: Device = Depends(current_device),
    db: Session = Depends(get_db),
):
    """Multipart JPEG frames; each filename is the capture time in epoch milliseconds."""
    s = get_settings()
    if s.evidence_key is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "evidence storage not configured; frames discarded by policy",
        )
    inc = _own_incident(db, device, incident_id)
    parsed: list[evidence.Frame] = []
    total = 0
    for f in frames:
        data = await f.read()
        total += len(data)
        if total > s.evidence_max_upload_bytes:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "upload too large")
        try:
            ms = int((f.filename or "").split(".")[0])
            at = datetime.fromtimestamp(ms / 1000, tz=UTC)
        except ValueError:
            raise HTTPException(422, "frame filename must be '<epoch_ms>.jpg'") from None
        parsed.append(evidence.Frame(at, data))
    try:
        clip = evidence.store(db, inc, segment, parsed)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except evidence.EvidenceUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None

    score = get_verifier().verify(parsed) if segment == "pre" else None
    if score is not None:
        incidents.observe(
            db, inc, incidents.ObservationInput(verifier_score=score), actor="verifier"
        )
    db.commit()
    return {"clip_id": clip.id, "frames": clip.frame_count, "verifier_score": score}
