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

log = get_logger("dashboard_api")

app = create_app()


def main():
    import uvicorn

    if not settings.REPO_URL or not settings.GITHUB_TOKEN:
        log.warning(
            "REPO_URL and/or GITHUB_TOKEN are not set -- the API will start, "
            "but every endpoint that reads state will report the state "
            "branch as unreachable until both are set."
        )

    log.info("starting dashboard API on %s:%s", settings.HOST, settings.PORT)
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)


if __name__ == "__main__":
    main()
