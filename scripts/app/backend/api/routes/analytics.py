from fastapi import APIRouter, Query

from app.backend.services import analytics_service
from app.backend.api.validation import validate_date

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/performance")
def performance(
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
):
    validate_date(start_date, "start_date")
    validate_date(end_date, "end_date")
    return analytics_service.get_performance(start_date, end_date)


@router.get("/statistics")
def statistics(
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
):
    validate_date(start_date, "start_date")
    validate_date(end_date, "end_date")
    return analytics_service.get_trade_statistics(start_date, end_date)


@router.get("/health-score")
def health_score(
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
):
    validate_date(start_date, "start_date")
    validate_date(end_date, "end_date")
    return analytics_service.get_scanner_health(start_date, end_date)
