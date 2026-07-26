"""
app.py
------
FastAPI application factory for the Phase 1 read-only dashboard API.
Import and run via run_dashboard_api.py at the repo root -- do not
run this module directly (it needs `scripts/` on sys.path the same
way main.py does).
"""

import asyncio
import contextlib
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.backend import settings
from app.backend.api.routes import (
    health, scanner, config as config_routes, dashboard, analytics, replay, live,
)
from app.backend.api.ws_manager import manager as ws_manager
from app.backend.services import live_service

log = logging.getLogger("trading_system.backend.api")

API_VERSION = "0.3.0-phase3"


def create_app() -> FastAPI:
    app = FastAPI(
        title="NSE Options Scanner -- Dashboard API",
        description=(
            "Read-only monitoring API over the existing scanner's state. "
            "Never places orders, never modifies broker positions, never "
            "writes to the state branch. Includes a poll-and-diff "
            "WebSocket (/ws/dashboard) for live updates -- see README.md "
            "'Phase 3'."
        ),
        version=API_VERSION,
    )

    # Permissive CORS for now (no auth on this API yet -- it's meant
    # to run on localhost / a private network, not be exposed
    # publicly; see README.md "Security" section). Tighten
    # allow_origins before any public deployment. Starlette's
    # CORSMiddleware only wraps the "http" ASGI scope, not
    # "websocket", so this has no effect on /ws/dashboard either way
    # -- that endpoint is equally open until Phase 8 adds auth.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        log.error("unhandled error on %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "internal error", "type": type(exc).__name__},
        )

    app.include_router(health.router)
    app.include_router(scanner.router)
    app.include_router(config_routes.router)
    app.include_router(dashboard.router)
    app.include_router(analytics.router)
    app.include_router(replay.router)
    app.include_router(live.router)

    @app.get("/api/version", tags=["meta"])
    def version():
        return {"api_version": API_VERSION}

    # Phase 3: the poll-and-diff loop is a single background task,
    # started once per process and cancelled cleanly on shutdown. It
    # never touches main.py -- see services/live_service.py and
    # README.md's "Phase 3" section for the poll-vs-publish decision.
    @app.on_event("startup")
    async def _start_live_poller():
        app.state.live_poll_task = asyncio.create_task(live_service.poll_loop(ws_manager))
        log.info(
            "Phase 3 poll-and-diff loop started (interval=%ss)",
            settings.WS_BROADCAST_INTERVAL_SECONDS,
        )

    @app.on_event("shutdown")
    async def _stop_live_poller():
        task = getattr(app.state, "live_poll_task", None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return app
