"""Incident evidence store: encrypted at rest, retention-bounded, access-audited.

No key configured → evidence is refused (HTTP 503), never stored in plaintext.

Container format (before encryption):
    4-byte big-endian header length | JSON header {"frames": [{"t": iso, "n": len}, ...]}
    | concatenated JPEG payloads
Encrypted with AES-256-GCM: 12-byte nonce | ciphertext+tag. AAD = incident id.

The filesystem backend is for single-node deployments; an object-store backend (S3/GCS/
Azure Blob with server-side encryption + this envelope) implements the same interface.
"""

import base64
import hashlib
import json
import os
import struct
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutil import utcnow
from app.models import EvidenceClip, Incident

JPEG_SOI = b"\xff\xd8"


class EvidenceUnavailable(RuntimeError):
    pass


@dataclass
class Frame:
    at: datetime
    data: bytes


def _key() -> bytes:
    k = get_settings().evidence_key
    if k is None:
        raise EvidenceUnavailable("evidence encryption key not configured")
    raw = base64.urlsafe_b64decode(k.get_secret_value())
    if len(raw) != 32:
        raise EvidenceUnavailable("evidence key must decode to 32 bytes")
    return raw


def _root() -> Path:
    return Path(get_settings().evidence_dir)


def pack(frames: list[Frame]) -> bytes:
    header = json.dumps(
        {"frames": [{"t": f.at.isoformat(), "n": len(f.data)} for f in frames]}
    ).encode()
    return struct.pack(">I", len(header)) + header + b"".join(f.data for f in frames)


def unpack(blob: bytes) -> list[Frame]:
    (hlen,) = struct.unpack(">I", blob[:4])
    header = json.loads(blob[4 : 4 + hlen])
    out, off = [], 4 + hlen
    for meta in header["frames"]:
        out.append(Frame(datetime.fromisoformat(meta["t"]), blob[off : off + meta["n"]]))
        off += meta["n"]
    return out


def store(db: Session, inc: Incident, segment: str, frames: list[Frame]) -> EvidenceClip:
    if not frames:
        raise ValueError("no frames")
    for f in frames:
        if not f.data.startswith(JPEG_SOI):
            raise ValueError("frames must be JPEG")
    key = _key()
    frames = sorted(frames, key=lambda f: f.at)
    plain = pack(frames)
    nonce = os.urandom(12)
    blob = nonce + AESGCM(key).encrypt(nonce, plain, str(inc.id).encode())
    clip_id = uuid.uuid4()
    rel = f"{inc.facility_id}/{inc.id}/{clip_id}.vaid"
    path = _root() / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(blob)
    tmp.replace(path)
    clip = EvidenceClip(
        id=clip_id,
        incident_id=inc.id,
        storage_key=rel,
        segment=segment,
        frame_count=len(frames),
        size_bytes=len(blob),
        sha256=hashlib.sha256(plain).hexdigest(),
        first_frame_at=frames[0].at,
        last_frame_at=frames[-1].at,
        expires_at=utcnow() + timedelta(days=get_settings().evidence_retention_days),
    )
    db.add(clip)
    return clip


def load(clip: EvidenceClip) -> list[Frame]:
    if clip.deleted_at is not None:
        raise FileNotFoundError("evidence deleted under retention policy")
    blob = (_root() / clip.storage_key).read_bytes()
    plain = AESGCM(_key()).decrypt(blob[:12], blob[12:], str(clip.incident_id).encode())
    return unpack(plain)


def purge_expired(db: Session, now: datetime | None = None) -> int:
    now = now or utcnow()
    clips = db.scalars(
        select(EvidenceClip).where(
            EvidenceClip.expires_at <= now, EvidenceClip.deleted_at.is_(None)
        )
    ).all()
    for c in clips:
        (_root() / c.storage_key).unlink(missing_ok=True)
        c.deleted_at = now
    return len(clips)
