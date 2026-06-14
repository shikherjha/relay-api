from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3100",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
