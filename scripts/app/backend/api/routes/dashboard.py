from fastapi import APIRouter

from app.backend.services import dashboard_service

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
def summary():
    """Single aggregated payload: health + scanner status + latest
    signals. Convenience endpoint for a landing screen; each part is
    also available individually under /api/health, /api/scanner/*,
    /api/signals/*."""
    return dashboard_service.get_summary()
