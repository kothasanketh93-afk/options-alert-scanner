from fastapi import APIRouter, HTTPException, Query

from app.backend.services import scanner_service

router = APIRouter(prefix="/api", tags=["scanner"])


@router.get("/scanner/status")
def scanner_status(date: str = Query(None, description="YYYY-MM-DD, defaults to today (IST)")):
    return scanner_service.get_status(date)


@router.get("/scanner/open-trades")
def open_trades(date: str = Query(None, description="YYYY-MM-DD, defaults to today (IST)")):
    return scanner_service.get_open_trades(date)


@router.get("/signals/latest")
def latest_signals(
    date: str = Query(None, description="YYYY-MM-DD, defaults to today (IST)"),
    limit: int = Query(20, ge=1, le=200),
):
    return scanner_service.get_latest_signals(date, limit)


@router.get("/signals/history")
def signal_history(date: str = Query(..., description="YYYY-MM-DD, required")):
    try:
        return scanner_service.get_signal_history(date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/signals/history/available-dates")
def available_dates(lookback_days: int = Query(None, ge=1, le=365)):
    return scanner_service.get_available_history_dates(lookback_days)
