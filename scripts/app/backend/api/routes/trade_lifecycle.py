from fastapi import APIRouter, Query

from app.backend.services import trade_lifecycle_service
from config import TRADE_LIFECYCLE_ENABLED, BTST_RECOMMENDATION_ENABLED

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


@router.get("/btst")
def btst(lookback_days: int = Query(10, ge=1, le=60)):
    if not TRADE_LIFECYCLE_ENABLED:
        return _disabled_response()
    return {"enabled": True, "positions": trade_lifecycle_service.get_btst_positions(lookback_days)}


@router.get("/stbt")
def stbt(lookback_days: int = Query(10, ge=1, le=60)):
    if not TRADE_LIFECYCLE_ENABLED:
        return _disabled_response()
    return {"enabled": True, "positions": trade_lifecycle_service.get_stbt_positions(lookback_days)}


@router.get("/carry-history")
def carry_history(lookback_days: int = Query(10, ge=1, le=60)):
    if not TRADE_LIFECYCLE_ENABLED:
        return _disabled_response()
    return {"enabled": True, "history": trade_lifecycle_service.get_carry_history(lookback_days)}


@router.get("/analytics")
def analytics(lookback_days: int = Query(10, ge=1, le=60)):
    if not TRADE_LIFECYCLE_ENABLED:
        return _disabled_response()
    result = trade_lifecycle_service.get_analytics(lookback_days)
    result["enabled"] = True
    result["btst_recommendation_enabled"] = BTST_RECOMMENDATION_ENABLED
    return result
