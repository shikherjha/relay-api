from fastapi import FastAPI

from app.core.config import settings
from app.routers import (
    cart,
    demo,
    health,
    lifeledger,
    ops,
    p2p,
    products,
    rescue,
    returns,
    users,
    warranty,
    wishlist,
)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Relay BFF — returns, disposition orchestration, matching, credits.",
)

for module in (
    health,
    products,
    users,
    cart,
    returns,
    rescue,
    wishlist,
    p2p,
    warranty,
    lifeledger,
    ops,
    demo,
):
    app.include_router(module.router)
