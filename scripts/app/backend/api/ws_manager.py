"""
ws_manager.py
--------------
Minimal in-memory WebSocket connection registry for Phase 3. Holds no
business logic -- just tracks which sockets are open and relays
messages live_service.poll_loop() hands it.

Single-process, single-worker assumption (matching how
run_dashboard_api.py runs uvicorn today -- see settings.py / README.md):
if this process is ever run with multiple uvicorn workers, each worker
gets its own ConnectionManager and its own poll_loop task, so a client
is only notified by whichever worker it happens to be connected to.
Call this out before adding multi-worker deployment; fixing it would
need a shared pub/sub layer (e.g. Redis) that does not exist in this
project yet.
"""

import asyncio
import logging

from fastapi import WebSocket

log = logging.getLogger("trading_system.backend.ws_manager")


class ConnectionManager:
    def __init__(self):
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        log.info("dashboard websocket connected (total=%d)", len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        log.info("dashboard websocket disconnected (total=%d)", len(self._connections))

    async def broadcast(self, message: dict) -> None:
        async with self._lock:
            targets = list(self._connections)

        dead = []
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# One instance per process, shared between the poll loop (api/app.py's
# startup hook) and the /ws/dashboard route (api/routes/live.py).
manager = ConnectionManager()
