"""In-process WebSocket fan-out, published only after the DB transaction commits.

Services call `queue_event(db, facility_id, message)`; an after_commit hook delivers it.
A rolled-back transaction never produces a real-time event.

Single-instance only. Horizontal scaling replaces `_deliver` with Redis pub/sub.
"""

import asyncio
import logging
import uuid
from collections import defaultdict

from fastapi import WebSocket
from sqlalchemy import event
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


class RealtimeHub:
    def __init__(self) -> None:
        self._conns: dict[uuid.UUID, set[WebSocket]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, facility_id: uuid.UUID, ws: WebSocket) -> None:
        self._conns[facility_id].add(ws)

    def disconnect(self, facility_id: uuid.UUID, ws: WebSocket) -> None:
        self._conns[facility_id].discard(ws)

    async def _broadcast(self, facility_id: uuid.UUID, message: dict) -> None:
        for ws in list(self._conns.get(facility_id, ())):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001 — a dead socket must not block others
                self.disconnect(facility_id, ws)

    def publish(self, facility_id: uuid.UUID, message: dict) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(self._broadcast(facility_id, message))
        else:
            asyncio.run_coroutine_threadsafe(self._broadcast(facility_id, message), loop)


hub = RealtimeHub()

_KEY = "visionaid_realtime"


def queue_event(db: Session, facility_id: uuid.UUID, message: dict) -> None:
    db.info.setdefault(_KEY, []).append((facility_id, message))


@event.listens_for(Session, "after_commit")
def _flush_realtime(session: Session) -> None:
    for facility_id, message in session.info.pop(_KEY, []):
        try:
            hub.publish(facility_id, message)
        except Exception:  # noqa: BLE001
            log.exception("realtime publish failed")


@event.listens_for(Session, "after_rollback")
def _drop_realtime(session: Session) -> None:
    session.info.pop(_KEY, None)
