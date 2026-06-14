from fastapi import FastAPI

from app.core.config import settings
from app.routers import health

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Relay BFF — returns, disposition orchestration, matching, credits.",
)

app.include_router(health.router)
