"""
dashboard_service.py
---------------------
Thin aggregator for a single "give me everything the landing screen
needs" call. Adds no logic of its own beyond composing the other
Phase 1 services -- kept separate so routes/dashboard.py doesn't need
to know about scanner/health internals directly.
"""

from app.backend.services import scanner_service, health_service


def get_summary() -> dict:
    return {
        "health": health_service.get_health(),
        "scanner_status": scanner_service.get_status(),
        "latest_signals": scanner_service.get_latest_signals(limit=10),
    }
