from fastapi import APIRouter

from app.backend.services import health_service

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
def health():
    """System health: state-branch reachability, circuit breaker,
    today's failure counters, and this dashboard process's own
    resource use. Read-only."""
    return health_service.get_health()
