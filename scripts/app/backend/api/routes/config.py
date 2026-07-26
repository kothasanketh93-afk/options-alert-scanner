from fastapi import APIRouter

from app.backend.services import configuration_service

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
def get_configuration():
    """Read-only snapshot of config.py's public constants. No reload
    /validate/import endpoints yet -- see backend/README.md."""
    return configuration_service.get_configuration()
