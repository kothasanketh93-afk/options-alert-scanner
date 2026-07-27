"""
run_dashboard_api.py
---------------------
Entry point for the Phase 1 read-only dashboard API. This is a
SEPARATE process from main.py / trader.yml -- it does not run inside
the GitHub Actions scanner job, does not import broker.py or auth.py,
and never writes to the state branch. Run it wherever you want to
view the dashboard from (a small VM, a container, or your own
machine), pointed at the same REPO_URL the scanner uses.

Usage:
    export REPO_URL=https://github.com/you/repo.git
    export GITHUB_TOKEN=<token with at least read access>
    pip install -r requirements-dashboard.txt
    python run_dashboard_api.py

Then: http://127.0.0.1:8800/api/health, /api/scanner/status,
/api/signals/latest, /api/signals/history?date=YYYY-MM-DD,
/api/config, /api/dashboard/summary, and interactive docs at
/docs (FastAPI's built-in Swagger UI).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from logging_setup import get_logger  # noqa: E402  -- after sys.path setup, same pattern as main.py
from app.backend import settings  # noqa: E402
from app.backend.api.app import create_app  # noqa: E402
from app.backend.api.ws_manager import manager as ws_manager  # noqa: E402
from production.startup_validator import run_startup_validation  # noqa: E402
from production.resource_monitor import resource_monitor  # noqa: E402
from production.task_manager import task_manager  # noqa: E402
from production.shutdown_manager import shutdown_manager  # noqa: E402

log = get_logger("dashboard_api")

app = create_app()


def _resource_sampler_worker(stop_event, heartbeat):
    """Part 9 item 6: periodically samples resources so
    GET /api/health/extended (production/health_aggregator.py) always
    has a recent snapshot rather than sampling cold on every request."""
    while not stop_event.is_set():
        _snapshot, alerts = resource_monitor.sample_and_evaluate()
        for alert in alerts:
            log.warning("resource alert: %s", alert.message)
        heartbeat()
        stop_event.wait(30)


def main():
    import uvicorn

    if not settings.REPO_URL or not settings.GITHUB_TOKEN:
        log.warning(
            "REPO_URL and/or GITHUB_TOKEN are not set -- the API will start, "
            "but every endpoint that reads state will report the state "
            "branch as unreachable until both are set."
        )

    # Part 9 item 4: this process never touches broker/Telegram
    # credentials by design (see module docstring), so those checks
    # are skipped here rather than reported as false failures.
    # raise_on_failure=False -- a dashboard process shouldn't refuse
    # to start over e.g. a missing optional folder; log the report
    # and continue, same tolerant posture as the REPO_URL/GITHUB_TOKEN
    # check above.
    try:
        startup_report = run_startup_validation(
            require_github=False, require_upstox=False, require_angelone=False,
            folders=["logs"], raise_on_failure=False,
        )
        if not startup_report.passed:
            log.warning("startup validation found issues:\n%s", startup_report.render())
    except Exception as exc:  # noqa: BLE001 - startup validation itself must never block the dashboard
        log.warning("startup validation could not complete: %s", exc)

    # Part 9 items 6 & 8: resource sampling + WebSocket connection
    # count feed GET /api/health/extended via the shared
    # resource_monitor/task_manager singletons also used by
    # app/backend/api/routes/health_extended.py.
    resource_monitor.register_gauge("ws_connections", ws_manager.connection_count)
    resource_monitor.register_gauge("background_task_count", lambda: task_manager.running_count)
    task_manager.register("resource_sampler", _resource_sampler_worker, heartbeat_timeout=90.0)
    task_manager.start("resource_sampler")

    # Part 9 item 13: graceful shutdown on CTRL+C/SIGTERM -- stops the
    # sampler worker before the process exits instead of leaving it as
    # an orphan (it's a daemon thread so it can't block exit either
    # way, but this keeps the stop explicit and logged).
    shutdown_manager.register("stop_background_tasks", lambda: task_manager.stop_all(timeout=5.0))

    log.info("starting dashboard API on %s:%s", settings.HOST, settings.PORT)
    try:
        uvicorn.run(app, host=settings.HOST, port=settings.PORT)
    finally:
        shutdown_manager.shutdown(reason="process_exit")


if __name__ == "__main__":
    main()
