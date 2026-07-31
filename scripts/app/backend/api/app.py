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
    health_extended, monitoring, diagnostics, trade_lifecycle as trade_lifecycle_routes,
)
from app.backend.api.ws_manager import manager as ws_manager
from app.backend.services import live_service
from production.api_protection import (
    RequestIDMiddleware, TimeoutMiddleware, error_envelope,
)
from production.api_monitor import APIMonitoringMiddleware, api_monitor
from production.websocket_monitor import websocket_monitor
from production.dashboard_monitor import dashboard_monitor
from production.health_registry import health_registry
from production.scanner_monitor import scanner_monitor
from production.broker_monitor import broker_monitor
from production.telegram_monitor import telegram_monitor
from production.task_manager import task_manager
from production.service_registry import service_registry
from app.engines.decision_engine import quant_adapter
from app.engines.decision_engine import news_adapter

log = logging.getLogger("trading_system.backend.api")

API_VERSION = "0.4.0-phase3-4-monitoring"


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

    # Part 9 item 7 (API Protection): request tracing + a hard ceiling
    # on how long any single request may run. Added outside-in, so
    # RequestIDMiddleware runs first and its request_id is available
    # to TimeoutMiddleware's 504 response and to the exception handler
    # below. Purely additive -- no route or service behavior changes.
    app.add_middleware(TimeoutMiddleware, timeout_seconds=settings.API_REQUEST_TIMEOUT_SECONDS)
    app.add_middleware(RequestIDMiddleware)
    # Part 10 Phase 3 (API Monitoring): added last so it wraps
    # everything below it (RequestID/Timeout run "inside" its timing),
    # matching APIMonitoringMiddleware's own docstring on why that
    # ordering was chosen. Purely observational -- never changes a
    # response, so this cannot change existing route behavior.
    if APIMonitoringMiddleware is not None:
        app.add_middleware(APIMonitoringMiddleware, monitor=api_monitor)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        log.error("unhandled error on %s %s: %s", request.method, request.url.path, exc)
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=500,
            content=error_envelope(500, type(exc).__name__, "internal error", request_id=request_id),
        )

    app.include_router(health.router)
    app.include_router(health_extended.router)
    app.include_router(scanner.router)
    app.include_router(config_routes.router)
    app.include_router(dashboard.router)
    app.include_router(analytics.router)
    app.include_router(replay.router)
    app.include_router(live.router)
    app.include_router(monitoring.router)
    app.include_router(diagnostics.router)
    app.include_router(trade_lifecycle_routes.router)

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

    # Part 10, Phase 6: service discovery -- registers all six Phase
    # 1-3 monitors with the shared health_registry (Part 10 item 1) in
    # one call and declares their dependency edges (health_registry
    # itself has no notion of "depends on"). This is also what wires
    # websocket/fastapi/dashboard into health_registry.check_all(),
    # which previously reported them as "not monitored" OFFLINE.
    # task_manager is folded in as the worker/background-task registry
    # -- see service_registry.py's docstring for why "worker registry"
    # and "background task registry" are the same accessor here.
    service_registry.health_registry = health_registry
    service_registry.task_manager = task_manager
    service_registry.register_known_components(
        scanner_status=scanner_monitor.health,
        broker_status=broker_monitor.health,
        telegram_status=telegram_monitor.health,
        websocket_status=websocket_monitor.health,
        api_status=api_monitor.health,
        dashboard_status=dashboard_monitor.health,
    )
    # Part 11 -- Quant Intelligence Engine: not one of register_known_
    # components()'s six fixed slots, so registered directly against
    # the same shared health_registry via the underlying primitive it
    # itself uses (health_registry.register(name, callable)) -- same
    # "not monitored until something registers for it" contract as
    # every other component (see production/health_registry.py).
    health_registry.register("quant_intelligence", quant_adapter.health)
    # Part 11.5 -- News Intelligence Engine: same "not monitored until
    # something registers for it" contract, mirroring the line above.
    health_registry.register("news_intelligence", news_adapter.health)

    @app.on_event("shutdown")
    async def _stop_live_poller():
        dashboard_monitor.set_available(False)
        task = getattr(app.state, "live_poll_task", None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return app
