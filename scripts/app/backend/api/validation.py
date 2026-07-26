from datetime import datetime

from fastapi import HTTPException


def validate_date(date_str: str, field_name: str = "date") -> str:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field_name} must be YYYY-MM-DD, got {date_str!r}")
    return date_str


def validate_year_month(value: str, field_name: str = "year_month") -> str:
    try:
        datetime.strptime(value, "%Y-%m")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field_name} must be YYYY-MM, got {value!r}")
    return value
