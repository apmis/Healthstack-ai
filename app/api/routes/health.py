from fastapi import APIRouter

from app.core.config import get_settings
from app.core.database import ping_database

router = APIRouter(tags=["health"])


@router.get("/health")
def healthcheck() -> dict[str, str]:
    ping_database()
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.app_name,
        "database": settings.mongodb_db,
    }

