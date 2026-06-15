"""Track B "Second Life" resell/republish orchestration.

Two on-ramps into one Second Life catalogue (`resale_listings`):

* p2p — a buyer re-lists a unit they own once its return window has expired.
* certified — a seller republishes a refurbished unit they got back.

Both call the ML boundary's `grade_and_price` (real Bedrock grade + price band,
with a deterministic local fallback), persist a Condition Passport if the unit
isn't graded yet, write the LifeLedger chain, and create the listing. Buying a
listing runs a stub escrow, transfers ownership, anchors P2P_SOLD, and awards
impact credits.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clients import s3_client
from app.clients.ledger_client import get_ledger_client
from app.clients.ml_client import MLClient
from app.core.carbon import credits_for_co2, net_co2_saved
from app.core.config import settings
from app.core.hashing import passport_hash as compute_hash
from app.models import entities as m
from app.schemas.ml import ConditionPassport, Verification
from app.services.grading import ensure_verification
from app.schemas.resale import (
    BuyResult,
    PriceRange,
    ResaleAssessment,
    ResaleListing as ResaleListingSchema,
    SellerOrderItem,
    SellerRefurbUnit,
)

CREDIT_UNLOCK_DAYS = 14
RESALE_CHANNEL = "p2p_resale"


def _product_color(product: m.Product | None) -> str | None:
    """Catalogue colour (product_metadata.color) for order-vs-item verification."""
    if product is None or not isinstance(product.product_metadata, dict):
        return None
    return product.product_metadata.get("color")


class ResaleError(ValueError):
    """Eligibility / guard failure → mapped to a 4xx in the router."""

    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


# ── time helpers ────────────────────────────────────────────────────────────
def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def compute_age_days(db: Session, unit: m.ProductUnit, order_item: m.OrderItem | None = None) -> int:
    """Days since the unit entered its first life (delivered / PURCHASED)."""
    now = datetime.now(timezone.utc)
    anchor: datetime | None = None
    if order_item is not None:
        anchor = _aware(order_item.delivered_at) or _aware(order_item.created_at)
    if anchor is None:
        ev = db.execute(
            select(m.LifeLedgerEvent)
            .where(m.LifeLedgerEvent.unit_id == unit.id)
            .where(m.LifeLedgerEvent.event_type == "PURCHASED")
            .order_by(m.LifeLedgerEvent.created_at.asc())
        ).scalars().first()
        if ev is not None:
            anchor = _aware(ev.created_at)
    if anchor is None:
        return 0
    return max(0, (now - anchor).days)


# ── lookups ─────────────────────────────────────────────────────────────────
def _latest_passport(db: Session, unit_id) -> m.ConditionPassport | None:
    return db.execute(
        select(m.ConditionPassport)
        .where(m.ConditionPassport.unit_id == unit_id)
        .order_by(m.ConditionPassport.graded_at.desc())
    ).scalars().first()


def _last_event_type(db: Session, unit_id) -> str | None:
    ev = db.execute(
        select(m.LifeLedgerEvent)
        .where(m.LifeLedgerEvent.unit_id == unit_id)
        .order_by(m.LifeLedgerEvent.created_at.desc())
    ).scalars().first()
    return ev.event_type if ev else None


def _has_event(db: Session, unit_id, event_type: str) -> bool:
    return db.execute(
        select(m.LifeLedgerEvent.id)
        .where(m.LifeLedgerEvent.unit_id == unit_id)
        .where(m.LifeLedgerEvent.event_type == event_type)
        .limit(1)
    ).first() is not None


def active_resale_listing(db: Session, unit_id) -> m.ResaleListing | None:
    return db.execute(
        select(m.ResaleListing)
        .where(m.ResaleListing.unit_id == unit_id)
        .where(m.ResaleListing.status == "active")
        .order_by(m.ResaleListing.created_at.desc())
    ).scalars().first()


def is_relistable(db: Session, unit: m.ProductUnit | None, seller_id) -> bool:
    """Single source of truth for certified-relist eligibility, shared by
    /seller/refurbished and /seller/orders so the two never drift apart:
    owned by the seller + graded (has a passport) + refurbished/returned/graded
    + not already listed for resale."""
    if unit is None:
        return False
    if not (unit.owner_id is not None and str(unit.owner_id) == str(seller_id)):
        return False
    if _latest_passport(db, unit.id) is None:
        return False
    refurbished = _has_event(db, unit.id, "REFURBISHED") or unit.status in (
        "graded", "refurbished", "returned",
    )
    if not refurbished:
        return False
    return active_resale_listing(db, unit.id) is None


def _seller_listing_for_unit(db: Session, unit_id, seller_id) -> m.ResaleListing | None:
    """The seller's resale listing for a unit (certified preferred, latest first)."""
    rows = db.execute(
        select(m.ResaleListing)
        .where(m.ResaleListing.unit_id == unit_id)
        .where(m.ResaleListing.lister_id == seller_id)
        .order_by(m.ResaleListing.created_at.desc())
    ).scalars().all()
    if not rows:
        return None
    return next((r for r in rows if r.source == "certified"), rows[0])


# ── return window (Track B order enrichment) ─────────────────────────────────
def order_item_window(db: Session, oi: m.OrderItem, order_user_id) -> dict:
    """Return-window state for one order line (delivered_at-anchored)."""
    now = datetime.now(timezone.utc)
    delivered_at = _aware(oi.delivered_at) or _aware(oi.created_at)

    ret = db.execute(
        select(m.ReturnEvent).where(m.ReturnEvent.order_item_id == oi.id)
        .order_by(m.ReturnEvent.created_at.desc())
    ).scalars().first()
    returned = ret is not None

    listed = oi.unit_id is not None and active_resale_listing(db, oi.unit_id) is not None

    days_to_deadline: int | None = None
    within_window = False
    if delivered_at is not None:
        elapsed_days = (now - delivered_at).days
        days_to_deadline = settings.return_window_days - elapsed_days
        within_window = days_to_deadline >= 0

    owned = False
    if oi.unit_id is not None:
        unit = db.get(m.ProductUnit, oi.unit_id)
        owned = unit is not None and unit.owner_id is not None and str(unit.owner_id) == str(order_user_id)

    returnable = within_window and not returned and not listed
    resellable = (delivered_at is not None and not within_window) and owned and not returned and not listed
    return {
        "delivered_at": delivered_at,
        "returnable": returnable,
        "resellable": resellable,
        "days_to_return_deadline": days_to_deadline,
        "returned": returned,
        "listed": listed,
        "return_id": str(ret.id) if ret is not None else None,
    }


# ── passport persistence ──────────────────────────────────────────────────────
def _store_passport_if_absent(
    db: Session, unit: m.ProductUnit, passport: ConditionPassport
) -> tuple[m.ConditionPassport, bool]:
    """Persist a passport + anchor GRADED only if the unit has none yet."""
    existing = _latest_passport(db, unit.id)
    if existing is not None:
        return existing, False

    payload = passport.model_dump(mode="json")
    payload["unit_id"] = str(unit.id)
    digest = compute_hash(payload)
    payload["passport_hash"] = digest
    row = m.ConditionPassport(unit_id=unit.id, passport=payload, passport_hash=digest)
    db.add(row)
    db.flush()
    anchor = get_ledger_client().anchor(unit_id=str(unit.id), passport_hash=digest)
    db.add(m.LifeLedgerEvent(
        unit_id=unit.id, event_type="GRADED", passport_hash=digest, tx_hash=anchor.tx_hash,
    ))
    return row, True


def _upload_resale_media(unit_id, media: list[tuple[str, bytes]], *, is_video: bool) -> list[str]:
    """Push reseller-uploaded photos/video to S3; return absolute URLs (best-effort)."""
    urls: list[str] = []
    for name, blob in media:
        ext = name.rsplit(".", 1)[-1].lower() if "." in (name or "") else ("mp4" if is_video else "jpg")
        if is_video:
            content_type = "video/mp4"
        else:
            content_type = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        key = f"{settings.s3_resale_prefix}/{unit_id}/{uuid.uuid4().hex}.{ext}"
        url = s3_client.upload_bytes(key, blob, content_type)
        if url:
            urls.append(url)
    return urls


def _create_listing(
    db: Session,
    *,
    unit: m.ProductUnit,
    lister_id,
    source: str,
    assessment: ResaleAssessment,
    media_urls: list[str] | None = None,
) -> m.ResaleListing:
    listing = m.ResaleListing(
        unit_id=unit.id,
        lister_id=lister_id,
        source=source,
        original_price=assessment.original_price,
        price_min=assessment.price_range.min,
        price_max=assessment.price_range.max,
        list_price=assessment.list_price,
        resale_grade=assessment.resale_grade,
        pricing_rationale=assessment.pricing_rationale,
        age_days=assessment.age_days,
        media_urls=media_urls or [],
        status="active",
        escrow_status="none",
    )
    db.add(listing)
    db.flush()
    return listing


# ── DTO ───────────────────────────────────────────────────────────────────────
def _lister_label(source: str, lister: m.User | None) -> str:
    if source == "certified":
        return "Relay Certified Refurbished"
    if lister is not None and lister.name:
        return f"Resold by {lister.name}"
    return "Community reseller"


def _passport_verification(passport: m.ConditionPassport | None) -> Verification | None:
    """Order-vs-item verification stored inside the passport JSON (if present)."""
    if passport is None or not isinstance(passport.passport, dict):
        return None
    raw = passport.passport.get("verification")
    if not raw:
        return None
    try:
        return Verification.model_validate(raw)
    except Exception:  # pragma: no cover - defensive
        return None


def resale_to_schema(db: Session, row: m.ResaleListing) -> ResaleListingSchema:
    unit = db.get(m.ProductUnit, row.unit_id)
    product = db.get(m.Product, unit.product_id) if unit is not None else None
    passport = _latest_passport(db, row.unit_id)
    grade = row.resale_grade
    if grade is None and passport is not None and isinstance(passport.passport, dict):
        grade = passport.passport.get("grade")
    lister = db.get(m.User, row.lister_id) if row.lister_id else None
    ships = row.source == "certified"
    original = float(row.original_price) if row.original_price is not None else (
        float(product.price) if product is not None else None
    )
    brand = None
    if product is not None and isinstance(product.product_metadata, dict):
        brand = product.product_metadata.get("brand")
    return ResaleListingSchema(
        id=str(row.id),
        unit_id=str(row.unit_id),
        source=row.source,
        title=product.title if product else None,
        brand=brand,
        category=product.category if product else None,
        vertical=product.vertical if product else None,
        image_url=product.image_url if product else None,
        media_urls=list(row.media_urls or []),
        resale_grade=grade,
        pricing_rationale=row.pricing_rationale,
        original_price=original,
        price_range=PriceRange(
            min=float(row.price_min) if row.price_min is not None else 0.0,
            max=float(row.price_max) if row.price_max is not None else 0.0,
        ),
        list_price=float(row.list_price) if row.list_price is not None else 0.0,
        age_days=row.age_days,
        lister_label=_lister_label(row.source, lister),
        ships=ships,
        fulfillment="shipped" if ships else "local_pickup",
        status=row.status,
        escrow_status=row.escrow_status,
        passport_id=str(passport.id) if passport is not None else None,
        lifeledger_unit_id=str(row.unit_id),
        verification=_passport_verification(passport),
    )


# ── p2p resell (buyer re-lists an out-of-window unit they own) ─────────────────
def resell_order_item(
    db: Session,
    ml: MLClient,
    *,
    order_item_id,
    caller_id,
    media: list[tuple[str, bytes]],
    is_video: bool = False,
) -> ResaleListingSchema:
    oi = db.get(m.OrderItem, order_item_id)
    if oi is None or oi.unit_id is None:
        raise ResaleError("order item not found", status_code=404)
    unit = db.get(m.ProductUnit, oi.unit_id)
    if unit is None:
        raise ResaleError("unit not found", status_code=404)
    order = db.get(m.Order, oi.order_id)

    # Ownership: the caller must both own the unit and own the purchase.
    if not (unit.owner_id is not None and str(unit.owner_id) == str(caller_id)):
        raise ResaleError("you do not own this unit", status_code=403)
    if order is not None and str(order.user_id) != str(caller_id):
        raise ResaleError("order does not belong to caller", status_code=403)

    window = order_item_window(db, oi, order.user_id if order else caller_id)
    if window["returned"]:
        raise ResaleError("item was returned and cannot be resold", status_code=409)
    if window["listed"]:
        raise ResaleError("item is already listed for resale", status_code=409)
    if window["returnable"]:
        raise ResaleError("item is still within its return window (not yet resellable)", status_code=409)

    if not media:
        raise ResaleError("at least one image (or a video) is required", status_code=422)

    product = db.get(m.Product, unit.product_id)
    category = product.category if product else "other"
    vertical = product.vertical if product else None
    original_price = float(oi.price) if oi.price is not None else (float(product.price) if product else 0.0)
    age_days = compute_age_days(db, unit, oi)
    expected_color = oi.variant or _product_color(product)
    product_title = product.title if product else None

    assessment = ml.grade_and_price(
        media, unit_id=str(unit.id), category=category,
        original_price=original_price, age_days=age_days, vertical=vertical, is_video=is_video,
        expected_size=oi.size, expected_color=expected_color, product_title=product_title,
    )
    ensure_verification(
        assessment.passport, expected_color=expected_color,
        expected_size=oi.size, product_title=product_title,
    )
    _store_passport_if_absent(db, unit, assessment.passport)
    media_urls = _upload_resale_media(unit.id, media, is_video=is_video)
    listing = _create_listing(
        db, unit=unit, lister_id=caller_id, source="p2p", assessment=assessment, media_urls=media_urls,
    )
    db.add(m.LifeLedgerEvent(unit_id=unit.id, event_type="P2P_LISTED"))
    db.commit()
    db.refresh(listing)
    return resale_to_schema(db, listing)


# ── certified relist (seller republishes a refurbished unit) ───────────────────
def seller_refurbished_units(db: Session, seller_id) -> list[SellerRefurbUnit]:
    """Units the seller sold (via order_items), got back + refurbished/graded,
    and can still relist (no active resale listing yet)."""
    units = db.execute(
        select(m.ProductUnit).where(m.ProductUnit.owner_id == seller_id)
    ).scalars().all()

    out: list[SellerRefurbUnit] = []
    for unit in units:
        was_sold = db.execute(
            select(m.OrderItem.id).where(m.OrderItem.unit_id == unit.id).limit(1)
        ).first() is not None
        if not was_sold:
            continue
        if not is_relistable(db, unit, seller_id):
            continue
        passport = _latest_passport(db, unit.id)

        product = db.get(m.Product, unit.product_id)
        grade = passport.passport.get("grade") if isinstance(passport.passport, dict) else None
        oi = db.execute(
            select(m.OrderItem).where(m.OrderItem.unit_id == unit.id)
            .order_by(m.OrderItem.created_at.asc())
        ).scalars().first()
        out.append(SellerRefurbUnit(
            unit_id=str(unit.id),
            title=product.title if product else None,
            category=product.category if product else None,
            vertical=product.vertical if product else None,
            image_url=product.image_url if product else None,
            original_price=float(oi.price) if oi and oi.price is not None else (
                float(product.price) if product else None
            ),
            age_days=compute_age_days(db, unit, oi),
            last_event=_last_event_type(db, unit.id),
            grade=grade,
        ))
    return out


def _derive_seller_status(
    db: Session, unit: m.ProductUnit | None, oi: m.OrderItem, listing: m.ResaleListing | None
) -> str:
    """unit/ledger reality: relisted/sold (has a listing) > refurbished > returned > delivered."""
    if listing is not None:
        return "sold" if listing.status == "sold" else "relisted"
    if unit is not None and (_has_event(db, unit.id, "REFURBISHED") or unit.status == "refurbished"):
        return "refurbished"
    returned = db.execute(
        select(m.ReturnEvent.id).where(m.ReturnEvent.order_item_id == oi.id).limit(1)
    ).first() is not None
    if returned or (unit is not None and (unit.status == "returned" or _has_event(db, unit.id, "RETURN_REQUESTED"))):
        return "returned"
    return "delivered"


def seller_orders(db: Session, seller_id) -> list[SellerOrderItem]:
    """Full seller order history: every sold unit the seller still holds (owned)
    or has put on the Second Life catalogue (listed), most-recent first."""
    owned = db.execute(
        select(m.OrderItem)
        .join(m.ProductUnit, m.ProductUnit.id == m.OrderItem.unit_id)
        .where(m.ProductUnit.owner_id == seller_id)
    ).scalars().all()
    listed = db.execute(
        select(m.OrderItem)
        .join(m.ResaleListing, m.ResaleListing.unit_id == m.OrderItem.unit_id)
        .where(m.ResaleListing.lister_id == seller_id)
    ).scalars().all()

    by_id: dict = {oi.id: oi for oi in owned}
    for oi in listed:
        by_id.setdefault(oi.id, oi)

    out: list[SellerOrderItem] = []
    for oi in by_id.values():
        unit = db.get(m.ProductUnit, oi.unit_id) if oi.unit_id else None
        product = db.get(m.Product, oi.product_id)
        order = db.get(m.Order, oi.order_id)
        listing = _seller_listing_for_unit(db, oi.unit_id, seller_id) if oi.unit_id else None
        buyer = db.get(m.User, order.user_id) if order is not None else None
        out.append(SellerOrderItem(
            order_id=str(oi.order_id),
            order_item_id=str(oi.id),
            unit_id=str(oi.unit_id) if oi.unit_id else None,
            title=product.title if product else None,
            category=product.category if product else None,
            vertical=product.vertical if product else None,
            image_url=product.image_url if product else None,
            sale_price=float(oi.price) if oi.price is not None else None,
            sold_at=order.placed_at if order is not None else None,
            delivered_at=_aware(oi.delivered_at),
            buyer_label=(buyer.name if buyer is not None and buyer.name else "Customer"),
            status=_derive_seller_status(db, unit, oi, listing),
            relistable=is_relistable(db, unit, seller_id),
            listing_id=str(listing.id) if listing is not None else None,
            age_days=compute_age_days(db, unit, oi) if unit is not None else None,
            last_event=_last_event_type(db, oi.unit_id) if oi.unit_id else None,
        ))
    out.sort(key=lambda s: s.sold_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out


def relist_unit(
    db: Session,
    ml: MLClient,
    *,
    unit_id,
    seller_id,
    media: list[tuple[str, bytes]],
    is_video: bool = False,
) -> ResaleListingSchema:
    unit = db.get(m.ProductUnit, unit_id)
    if unit is None:
        raise ResaleError("unit not found", status_code=404)
    if not (unit.owner_id is not None and str(unit.owner_id) == str(seller_id)):
        raise ResaleError("seller does not hold this unit", status_code=403)
    if active_resale_listing(db, unit.id) is not None:
        raise ResaleError("unit is already listed for resale", status_code=409)
    if not media:
        raise ResaleError("at least one image (or a video) is required", status_code=422)

    product = db.get(m.Product, unit.product_id)
    category = product.category if product else "other"
    vertical = product.vertical if product else None
    # Original price from the unit's sale line if known, else catalogue price.
    oi = db.execute(
        select(m.OrderItem).where(m.OrderItem.unit_id == unit.id)
        .order_by(m.OrderItem.created_at.asc())
    ).scalars().first()
    original_price = float(oi.price) if oi and oi.price is not None else (float(product.price) if product else 0.0)
    age_days = compute_age_days(db, unit, oi)
    expected_color = (oi.variant if oi else None) or _product_color(product)
    product_title = product.title if product else None
    expected_size = oi.size if oi else unit.size

    assessment = ml.grade_and_price(
        media, unit_id=str(unit.id), category=category,
        original_price=original_price, age_days=age_days, vertical=vertical, is_video=is_video,
        expected_size=expected_size, expected_color=expected_color, product_title=product_title,
    )
    ensure_verification(
        assessment.passport, expected_color=expected_color,
        expected_size=expected_size, product_title=product_title,
    )
    _store_passport_if_absent(db, unit, assessment.passport)

    # Certified republish provenance: REFURBISHED + RELISTED, only if missing.
    if not _has_event(db, unit.id, "REFURBISHED"):
        db.add(m.LifeLedgerEvent(unit_id=unit.id, event_type="REFURBISHED"))
    if not _has_event(db, unit.id, "RELISTED"):
        db.add(m.LifeLedgerEvent(unit_id=unit.id, event_type="RELISTED"))

    unit.status = "refurbished"
    media_urls = _upload_resale_media(unit.id, media, is_video=is_video)
    listing = _create_listing(
        db, unit=unit, lister_id=seller_id, source="certified", assessment=assessment, media_urls=media_urls,
    )
    db.add(m.LifeLedgerEvent(unit_id=unit.id, event_type="P2P_LISTED"))
    db.commit()
    db.refresh(listing)
    return resale_to_schema(db, listing)


# ── second-life catalogue ──────────────────────────────────────────────────────
def second_life(
    db: Session, *, vertical: str | None = None, category: str | None = None
) -> list[ResaleListingSchema]:
    rows = db.execute(
        select(m.ResaleListing).where(m.ResaleListing.status == "active")
        .order_by(m.ResaleListing.created_at.desc())
    ).scalars().all()
    out: list[ResaleListingSchema] = []
    for row in rows:
        dto = resale_to_schema(db, row)
        if vertical and dto.vertical != vertical:
            continue
        if category and dto.category != category:
            continue
        out.append(dto)
    return out


# ── buy (stub escrow + ownership transfer + ledger + credits) ──────────────────
def buy_listing(db: Session, *, listing_id, buyer_id) -> BuyResult:
    listing = db.get(m.ResaleListing, listing_id)
    if listing is None:
        raise ResaleError("listing not found", status_code=404)
    if listing.status != "active":
        raise ResaleError(f"listing is {listing.status}", status_code=409)
    if listing.lister_id is not None and str(listing.lister_id) == str(buyer_id):
        raise ResaleError("cannot buy your own listing", status_code=400)

    unit = db.get(m.ProductUnit, listing.unit_id)
    if unit is None:
        raise ResaleError("unit not found", status_code=404)
    if unit.transfer_count >= settings.chain_depth_cap:
        raise ResaleError(
            f"chain_depth_cap({unit.transfer_count}>={settings.chain_depth_cap})", status_code=409,
        )

    # Stub payment/escrow: none → held → released (no real PSP for the demo).
    listing.escrow_status = "held"
    db.flush()

    # Ownership transfer — the unit gets a new life under a new owner.
    unit.owner_id = buyer_id
    unit.transfer_count = (unit.transfer_count or 0) + 1
    unit.status = "sold"

    passport = _latest_passport(db, unit.id)
    digest = passport.passport_hash if passport is not None else None
    anchor = get_ledger_client().anchor(unit_id=str(unit.id), passport_hash=digest or "")
    db.add(m.LifeLedgerEvent(
        unit_id=unit.id, event_type="P2P_SOLD", passport_hash=digest, tx_hash=anchor.tx_hash,
    ))

    listing.status = "sold"
    listing.sold_to = buyer_id
    listing.escrow_status = "released"

    # Impact: a second-life purchase avoids a new-make + restock cycle.
    co2 = net_co2_saved(RESALE_CHANNEL)
    db.add(m.ImpactEvent(user_id=buyer_id, unit_id=unit.id, channel=RESALE_CHANNEL, co2_saved_kg=co2))
    credits = credits_for_co2(co2)
    if credits > 0:
        db.add(m.GreenCreditLedger(
            user_id=buyer_id, amount=credits, reason=f"second_life:{RESALE_CHANNEL}",
            unlock_at=datetime.now(timezone.utc) + timedelta(days=CREDIT_UNLOCK_DAYS),
        ))
    db.commit()

    return BuyResult(
        ok=True, listing_id=str(listing.id), escrow_status="released",
        new_owner_id=str(buyer_id), tx_hash=anchor.tx_hash,
    )
