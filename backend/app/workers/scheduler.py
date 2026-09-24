"""Periodic safety work: escalation, potential-fall expiry, device health, retention.

Runs inside the API process by default (single-node MVP). For multi-node deployments set
VISIONAID_RUN_WORKERS_IN_PROCESS=false and run `python -m app.workers.scheduler` as its
own service; row locks (FOR UPDATE SKIP LOCKED) make concurrent workers safe.
"""

import asyncio
import logging
import time

from app.core.config import get_settings
from app.database.session import session_factory
from app.services import devices, evidence, incidents

log = logging.getLogger("visionaid.worker")

_last_purge = 0.0


def run_once() -> dict:
    global _last_purge
    out = {}
    jobs = [
        ("escalated", incidents.run_escalation_cycle),
        ("expired", incidents.run_expiry_cycle),
        ("device_changes", devices.sweep_health),
    ]
    if time.monotonic() - _last_purge > 3600:
        jobs.append(("evidence_purged", evidence.purge_expired))
        _last_purge = time.monotonic()
    for name, job in jobs:
        # Each job commits independently: one failing job must not block escalation.
        with session_factory()() as db:
            try:
                out[name] = job(db)
                db.commit()
            except Exception:
                db.rollback()
                log.exception("worker job %s failed", name)
    return out


async def run_forever(stop: asyncio.Event) -> None:
    interval = get_settings().worker_interval_seconds
    while not stop.is_set():
        await asyncio.to_thread(run_once)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_forever(asyncio.Event()))
