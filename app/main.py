from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.routers import (
    cart,
    demo,
    health,
    lifeledger,
    ops,
    orders,
    p2p,
    products,
    resale,
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

_cors_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3100",
    *[o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()],
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (
    health,
    products,
    users,
    cart,
    orders,
    returns,
    resale,
    rescue,
    wishlist,
    p2p,
    warranty,
    lifeledger,
    ops,
    demo,
):
    app.include_router(module.router)

# Serve seeded product photos so `image_url` (/static/products/<file>) resolves.
# These are the same images used as return/resell grading inputs.
_PRODUCT_IMAGES_DIR = Path(__file__).resolve().parents[1] / "seed_assets" / "images"
_PRODUCT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static/products", StaticFiles(directory=str(_PRODUCT_IMAGES_DIR)), name="product-images")
