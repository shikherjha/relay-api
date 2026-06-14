"""Track B "Second Life" — resell, republish, browse, and buy.

Endpoints (locked contract):
  POST /orders/items/{order_item_id}/resell  — buyer re-lists an out-of-window unit
  GET  /second-life                           — combined p2p + certified catalogue
  POST /second-life/{listing_id}/buy          — stub escrow + ownership transfer
  GET  /seller/refurbished                    — seller's relist-eligible units
  POST /seller/units/{unit_id}/relist         — seller certified republish
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from sqlalchemy.orm import Session

from app.clients.ml_client import MLClient
from app.core.config import settings
from app.core.deps import current_user_id, ml_client
from app.core.ids import to_uuid
from app.db.session import get_db
from app.schemas.ml import Vertical
from app.schemas.resale import BuyResult, ResaleListing, SellerOrderItem, SellerRefurbUnit
from app.services import resale as resale_svc
from app.services.resale import ResaleError

router = APIRouter(tags=["second-life"])


async def _read_media(files: list[UploadFile]) -> tuple[list[tuple[str, bytes]], bool]:
    """Read 1-8 images, or a single video, into (name, bytes) tuples."""
    media: list[tuple[str, bytes]] = []
    is_video = False
    for f in files:
        blob = await f.read()
        if not blob:
            continue
        media.append((f.filename or "upload", blob))
        if (f.content_type or "").startswith("video/"):
            is_video = True
    if not media:
        raise HTTPException(status_code=422, detail="no media uploaded")
    if not is_video and len(media) > settings.resale_max_images:
        raise HTTPException(
            status_code=422, detail=f"max {settings.resale_max_images} images per listing",
        )
    return media, is_video


@router.post("/orders/items/{order_item_id}/resell", response_model=ResaleListing, status_code=201)
async def resell_item(
    order_item_id: str,
    files: list[UploadFile] = File(...),
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
    ml: MLClient = Depends(ml_client),
) -> ResaleListing:
    media, is_video = await _read_media(files)
    try:
        return resale_svc.resell_order_item(
            db, ml,
            order_item_id=to_uuid(order_item_id, what="order item id"),
            caller_id=to_uuid(user_id, what="user id"),
            media=media, is_video=is_video,
        )
    except ResaleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/second-life", response_model=list[ResaleListing])
def second_life(
    vertical: Vertical | None = Query(default=None),
    category: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[ResaleListing]:
    return resale_svc.second_life(db, vertical=vertical, category=category)


@router.post("/second-life/{listing_id}/buy", response_model=BuyResult)
def buy_second_life(
    listing_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> BuyResult:
    try:
        return resale_svc.buy_listing(
            db,
            listing_id=to_uuid(listing_id, what="listing id"),
            buyer_id=to_uuid(user_id, what="user id"),
        )
    except ResaleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/seller/orders", response_model=list[SellerOrderItem])
def seller_order_history(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> list[SellerOrderItem]:
    """Full seller order history (every sold unit), most-recent first. The
    relist affordance is the `relistable` subset surfaced by /seller/refurbished."""
    return resale_svc.seller_orders(db, to_uuid(user_id, what="user id"))


@router.get("/seller/refurbished", response_model=list[SellerRefurbUnit])
def seller_refurbished(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> list[SellerRefurbUnit]:
    return resale_svc.seller_refurbished_units(db, to_uuid(user_id, what="user id"))


@router.post("/seller/units/{unit_id}/relist", response_model=ResaleListing, status_code=201)
async def relist_unit(
    unit_id: str,
    files: list[UploadFile] = File(...),
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
    ml: MLClient = Depends(ml_client),
) -> ResaleListing:
    media, is_video = await _read_media(files)
    try:
        return resale_svc.relist_unit(
            db, ml,
            unit_id=to_uuid(unit_id, what="unit id"),
            seller_id=to_uuid(user_id, what="user id"),
            media=media, is_video=is_video,
        )
    except ResaleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
