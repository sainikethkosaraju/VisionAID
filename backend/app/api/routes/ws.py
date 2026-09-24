from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.api.deps import user_from_token
from app.database.session import session_factory
from app.services.realtime import hub

router = APIRouter()


@router.websocket("/ws")
async def realtime(ws: WebSocket):
    """Facility-scoped live events. Auth: first message {"token": "<JWT>"}.

    The token is sent as a message rather than a query string so it never lands in
    proxy or access logs.
    """
    await ws.accept()
    try:
        first = await ws.receive_json()
        with session_factory()() as db:
            user = user_from_token(db, str(first.get("token", "")))
            facility_id = user.facility_id
    except (HTTPException, ValueError, KeyError, AttributeError):
        await ws.close(code=4401)
        return
    except WebSocketDisconnect:
        return
    await hub.connect(facility_id, ws)
    await ws.send_json({"type": "ready"})
    try:
        while True:
            await ws.receive_text()  # keepalive / ignored
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(facility_id, ws)
