from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import copilot, health, patients, session
from app.core.config import get_settings
from app.core.database import close_mongo_client, ping_database


@asynccontextmanager
async def lifespan(_: FastAPI):
    ping_database()
    try:
        yield
    finally:
        close_mongo_client()


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(session.router, prefix="/api/v1")
app.include_router(patients.router, prefix="/api/v1")
app.include_router(copilot.router, prefix="/api/v1")
