from pathlib import Path

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

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

logger = logging.getLogger("relay.api")

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Relay BFF — returns, disposition orchestration, matching, credits.",
)


async def _catch_unhandled(request: Request, call_next):
    """Convert any unhandled exception into a JSON 500 *inside* the CORS layer.

    Starlette's default ServerErrorMiddleware sits OUTSIDE CORSMiddleware, so a
    raw unhandled exception yields a 500 with no Access-Control-Allow-Origin
    header — the browser then reports a misleading CORS / "Failed to fetch"
    error that masks the real server fault. Catching here (this middleware is
    inner to CORS) means the 500 response flows back out through CORSMiddleware
    and keeps its CORS headers, so the real error is visible to the client.
    """
    try:
        return await call_next(request)
    except Exception:  # noqa: BLE001 - last-resort guard so CORS headers survive
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "internal server error"})


_cors_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3100",
    *[o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()],
]

# Order matters: add the catch-all FIRST so the CORSMiddleware (added next) ends
# up OUTERMOST and wraps the JSON 500 with the proper CORS headers.
app.add_middleware(BaseHTTPMiddleware, dispatch=_catch_unhandled)

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
