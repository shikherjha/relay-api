from __future__ import annotations

from datetime import datetime, timezone

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.clients import s3_client
from app.clients.engine_client import EngineClient
from app.clients.ml_client import MLClient
from app.core.config import settings
from app.core.deps import current_user_id, engine_client, ml_client
from app.core.ids import to_uuid
from app.db.session import get_db
from app.models import entities as m
from app.schemas.disposition import DispositionRequest, DispositionResponse
from app.schemas.ml import ConditionPassport
from app.schemas.returns import MediaAccepted, ReturnCreate, ReturnEvent
from app.schemas.common import Geo
from app.services.disposition import build_demand_signal, record_outcome
from app.services.grading import grade_and_store
from app.services.rescue import create_listing_for_disposition

router = APIRouter(prefix="/returns", tags=["returns"])


def _load_return(db: Session, return_id: str) -> m.ReturnEvent:
    row = db.get(m.ReturnEvent, to_uuid(return_id, what="return id"))
    if row is None:
        raise HTTPException(status_code=404, detail="return not found")
    return row


@router.post("", response_model=ReturnEvent, status_code=201)
def create_return(
    payload: ReturnCreate,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> ReturnEvent:
    # Order-linked path (preferred): return what you actually bought. Resolves
    # the unit + owner from the purchased line. Falls back to a raw unit_id.
    order_item = None
    if payload.order_item_id:
        order_item = db.get(m.OrderItem, to_uuid(payload.order_item_id, what="order item id"))
        if order_item is None or order_item.unit_id is None:
            raise HTTPException(status_code=404, detail="order item not found")
        unit = db.get(m.ProductUnit, order_item.unit_id)
        existing = db.execute(
            select(m.ReturnEvent.id).where(m.ReturnEvent.order_item_id == order_item.id)
        ).first()
        if existing is not None:
            raise HTTPException(status_code=409, detail="order item already returned")
    elif payload.unit_id:
        unit = db.get(m.ProductUnit, to_uuid(payload.unit_id, what="unit id"))
    else:
        raise HTTPException(status_code=422, detail="order_item_id or unit_id required")
    if unit is None:
        raise HTTPException(status_code=404, detail="unit not found")

    now = datetime.now(timezone.utc)

    # WRONG_ITEM is fully GATED: the buyer got the wrong product, so there is
    # nothing to grade. Record a flagged return-to-seller and STOP — no grade,
    # no ConditionPassport / GRADED anchor, no unit mutation, no listing.
    if payload.reason_code == "wrong_item":
        row = m.ReturnEvent(
            unit_id=unit.id,
            order_item_id=order_item.id if order_item else None,
            user_id=to_uuid(payload.user_id or user_id, what="user id"),
            reason_code=payload.reason_code,
            status="flagged",
            pickup_slot=payload.pickup_slot,
        )
        db.add(row)
        if order_item is not None:
            order_item.return_state = "return_to_seller"
        db.commit()
        db.refresh(row)
        return ReturnEvent(
            id=str(row.id), unit_id=str(row.unit_id),
            order_item_id=str(row.order_item_id) if row.order_item_id else None,
            user_id=str(row.user_id) if row.user_id else None,
            reason_code=row.reason_code, status=row.status,
            pickup_slot=row.pickup_slot, pickup_at=row.pickup_at, created_at=row.created_at,
        )

    # Pickup-anchored reverse logistics: the rescue TTL clock starts at pickup.
    # For the demo we record pickup at request time so downstream flows are live.
    row = m.ReturnEvent(
        unit_id=unit.id,
        order_item_id=order_item.id if order_item else None,
        user_id=to_uuid(payload.user_id or user_id, what="user id"),
        reason_code=payload.reason_code,
        status="picked_up" if payload.pickup_slot else "initiated",
        pickup_slot=payload.pickup_slot,
        pickup_at=now if payload.pickup_slot else None,
    )
    db.add(row)
    # The unit is on its way back to Relay → eligible to re-enter circulation.
    unit.status = "returned"
    db.add(m.LifeLedgerEvent(unit_id=unit.id, event_type="RETURN_REQUESTED"))
    if payload.pickup_slot:
        db.add(m.LifeLedgerEvent(unit_id=unit.id, event_type="PICKED_UP"))
    db.commit()
    db.refresh(row)
    return ReturnEvent(
        id=str(row.id), unit_id=str(row.unit_id),
        order_item_id=str(row.order_item_id) if row.order_item_id else None,
        user_id=str(row.user_id) if row.user_id else None,
        reason_code=row.reason_code, status=row.status,
        pickup_slot=row.pickup_slot, pickup_at=row.pickup_at, created_at=row.created_at,
    )


@router.post("/{return_id}/media", response_model=MediaAccepted, status_code=202)
async def upload_media(
    return_id: str,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    ml: MLClient = Depends(ml_client),
) -> MediaAccepted:
    return_event = _load_return(db, return_id)
    unit = db.get(m.ProductUnit, return_event.unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="unit not found")

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

    # Persist the raw return media to S3 (best-effort; falls back to none).
    media_urls: list[str] = []
    for name, blob in media:
        ext = name.rsplit(".", 1)[-1].lower() if "." in (name or "") else ("mp4" if is_video else "jpg")
        content_type = "video/mp4" if is_video else ("image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}")
        key = f"{settings.s3_returns_prefix}/{return_id}/{uuid.uuid4().hex}.{ext}"
        url = s3_client.upload_bytes(key, blob, content_type)
        if url:
            media_urls.append(url)

    # Size hint from the linked order line (improves embedding for matching) +
    # expected order context (size/colour/title) for order-vs-item verification.
    size = None
    expected_color = None
    if return_event.order_item_id is not None:
        oi = db.get(m.OrderItem, return_event.order_item_id)
        if oi is not None:
            size = oi.size
            expected_color = oi.variant
    product = db.get(m.Product, unit.product_id)
    product_title = product.title if product else None
    if not expected_color and product is not None and isinstance(product.product_metadata, dict):
        expected_color = product.product_metadata.get("color")

    # Synchronous grade for demo determinism; swap to grade_return_task.delay() for async.
    # media_urls are folded into the passport BEFORE hashing (in grade_and_store) so
    # they're persisted durably without breaking LifeLedger verification.
    row = grade_and_store(
        db, ml, return_event=return_event, unit=unit, media=media, is_video=is_video,
        size=size, media_urls=media_urls, return_reason=return_event.reason_code,
        expected_size=size, expected_color=expected_color, product_title=product_title,
    )
    return MediaAccepted(
        job_id=f"job-{return_id}", status="graded",
        passport_id=str(row.id), media_hashes=row.passport.get("media_hashes", []),
        media_urls=media_urls,
    )


@router.get("/{return_id}/passport", response_model=ConditionPassport)
def get_passport(return_id: str, db: Session = Depends(get_db)) -> ConditionPassport:
    return_event = _load_return(db, return_id)
    row = db.execute(
        select(m.ConditionPassport)
        .where(m.ConditionPassport.return_id == return_event.id)
        .order_by(desc(m.ConditionPassport.graded_at))
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="passport not found (grade media first)")
    return ConditionPassport.model_validate(row.passport)


@router.post("/{return_id}/disposition", response_model=DispositionResponse)
def compute_disposition(
    return_id: str,
    db: Session = Depends(get_db),
    engine: EngineClient = Depends(engine_client),
) -> DispositionResponse:
    return_event = _load_return(db, return_id)
    unit = db.get(m.ProductUnit, return_event.unit_id)
    passport_row = db.execute(
        select(m.ConditionPassport)
        .where(m.ConditionPassport.return_id == return_event.id)
        .order_by(desc(m.ConditionPassport.graded_at))
    ).scalars().first()
    if unit is None or passport_row is None:
        raise HTTPException(status_code=409, detail="grade the return before disposition")

    passport = ConditionPassport.model_validate(passport_row.passport)
    demand = build_demand_signal(db, category=passport.category or "other", unit=unit)
    geo = Geo(lat=unit.geo_lat, lng=unit.geo_lng) if unit.geo_lat is not None else None

    # Exchange-first availability: another in-stock unit of the same product.
    exchange_available = db.execute(
        select(m.ProductUnit.id)
        .where(m.ProductUnit.product_id == unit.product_id)
        .where(m.ProductUnit.id != unit.id)
        .where(m.ProductUnit.status == "in_stock")
        .limit(1)
    ).first() is not None

    req = DispositionRequest(
        unit_id=str(unit.id), passport=passport,
        return_reason=return_event.reason_code, user_id=str(return_event.user_id) if return_event.user_id else None,
        geo=geo, demand=demand,
        transfer_count=unit.transfer_count, exchange_available=exchange_available,
    )
    decision = engine.score_disposition(req)
    record_outcome(
        db, user_id=return_event.user_id, unit=unit, decision=decision,
        passport_hash=passport_row.passport_hash,
    )

    # Two-path disposition → list it. Path A (local rescue) is pickup-anchored;
    # Path B (national certified relist) covers refurb / no-local-demand.
    # p2p_resale dispositions also surface the unit on the rescue feed so a
    # confirmed return is immediately visible (and trackable) rather than vanishing.
    if decision.channel in ("rescue", "refurb", "refurbish", "p2p_resale", "p2p"):
        anchored = return_event.pickup_at or return_event.created_at
        # SIZE-RETURN WINS: a pristine size/fit return is re-listed at only a
        # minimal markdown (near-original price), not the standard rescue base.
        discount_pct = (
            settings.size_return_minimal_discount_pct
            if return_event.reason_code in set(settings.size_return_reasons)
            else None
        )
        create_listing_for_disposition(
            db, unit=unit, channel=decision.channel, anchored_at=anchored,
            has_local_demand=bool(demand and demand.open_wish_count > 0),
            discount_pct=discount_pct,
        )
        db.commit()
    return decision
