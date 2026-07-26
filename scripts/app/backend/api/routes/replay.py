from fastapi import APIRouter, Query

from app.backend.services import replay_service
from app.backend.api.validation import validate_date, validate_year_month

router = APIRouter(prefix="/api/replay", tags=["replay"])


@router.get("/journal")
def journal(date: str = Query(..., description="YYYY-MM-DD")):
    validate_date(date)
    return replay_service.get_journal(date)


@router.get("/journal/range")
def journal_range(
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
):
    validate_date(start_date, "start_date")
    validate_date(end_date, "end_date")
    return replay_service.get_journal_range(start_date, end_date)


@router.get("/summary/daily")
def daily_summary(date: str = Query(..., description="YYYY-MM-DD")):
    validate_date(date)
    return replay_service.get_daily_summary(date)


@router.get("/summary/weekly")
def weekly_summary(date: str = Query(..., description="Any YYYY-MM-DD within the target week")):
    validate_date(date)
    return replay_service.get_weekly_summary(date)


@router.get("/summary/monthly")
def monthly_summary(year_month: str = Query(..., description="YYYY-MM")):
    validate_year_month(year_month)
    return replay_service.get_monthly_summary(year_month)
