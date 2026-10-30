from fastapi import APIRouter

from app.backend.services import trade_lifecycle_service
from config import TRADE_LIFECYCLE_ENABLED

router = APIRouter(prefix="/api/lifecycle", tags=["trade_lifecycle"])


def _disabled_response():
    return {"enabled": False, "reason": "config.TRADE_LIFECYCLE_ENABLED is False"}


@router.get("/open-trades")
def open_trades():
    if not TRADE_LIFECYCLE_ENABLED:
        return _disabled_response()
    return {"enabled": True, "trades": trade_lifecycle_service.get_open_trades()}


@router.get("/closed-today")
def closed_today():
    if not TRADE_LIFECYCLE_ENABLED:
        return _disabled_response()
    return {"enabled": True, "trades": trade_lifecycle_service.get_closed_today()}


@router.get("/open-at-eod")
def open_at_eod():
    if not TRADE_LIFECYCLE_ENABLED:
        return _disabled_response()
    return {"enabled": True, "trades": trade_lifecycle_service.get_open_at_eod()}


@router.get("/analytics")
def analytics():
    if not TRADE_LIFECYCLE_ENABLED:
        return _disabled_response()
    result = trade_lifecycle_service.get_analytics()
    result["enabled"] = True
    return result
