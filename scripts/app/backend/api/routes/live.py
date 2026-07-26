"""
live.py
--------
Phase 3 WebSocket endpoint. Poll-and-diff (see README.md's "Phase 3"
section for the design decision and its rationale) -- this route never
triggers or touches anything in main.py; it only relays whatever
live_service.compute_diff_events() (itself just Phase 1/2 services
already used by the REST routes) finds changed on each poll cycle.

Message shape sent to every client: {"type": "<event_type>", "data": ...}.
See services/live_service.py's module docstring for the full list of
event types and the honest limitations (no fabricated "analytics
progress" event).

Reconnect is a client-side concern -- Phase 4 (the React dashboard)
isn't built yet, so there's no client to wire this into in this pass.
Documented here for whoever builds that: treat any close or error as a
signal to reconnect with backoff, and expect a fresh full snapshot
immediately on the new connection, since this endpoint always sends
one right after accept -- no need to track offsets or replay history
client-side.
"""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.backend.services import live_service
from app.backend.api.ws_manager import manager

log = logging.getLogger("trading_system.backend.api.live")

router = APIRouter(tags=["live"])


@router.websocket("/ws/dashboard")
async def ws_dashboard(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        initial_events = await asyncio.to_thread(live_service.build_full_snapshot)
        for event in initial_events:
            await websocket.send_json(event)

        # Push-only endpoint -- there are no client->server commands in
        # Phase 3. Still need to await something so we notice a
        # disconnect (or a client-sent ping/pong keepalive) rather than
        # returning immediately.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        log.debug("dashboard websocket closed by client")
    finally:
        await manager.disconnect(websocket)
