"""Order-line actions: gated wrong_item return + in-window exchange.

Two locked endpoints sit on top of these (orders router):
* POST /orders/items/{id}/return  — order-line return. ``wrong_item`` is fully
  GATED (no grade, no passport/GRADED anchor, no listing → flagged
  return-to-seller). Other reasons fall through to a normal order-linked return.
* POST /orders/items/{id}/exchange — in-window exchange, NO ML grading: mint a
  replacement line, mark the returned unit pristine, auto-list it on Path-A
  rescue at original/minimal discount (pickup-anchored) to cut emissions, and
  anchor an EXCHANGED LifeLedger event.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.ledger_client import get_ledger_client
from app.core.carbon import net_co2_saved
from app.core.config import settings
from app.core.hashing import passport_hash as compute_hash
from app.models import entities as m
from app.schemas.orders import (
    ExchangeReplacement,
    ExchangeRescueListing,
    ExchangeResult,
)
from app.services.rescue import create_listing_for_disposition
from app.services.resale import order_item_window


class OrderActionError(ValueError):
    """Eligibility / guard failure → mapped to a 4xx in the router."""

    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


def _owned_line(db: Session, order_item_id, caller_id) -> tuple[m.OrderItem, m.ProductUnit, m.Order | None]:
    oi = db.get(m.OrderItem, order_item_id)
    if oi is None or oi.unit_id is None:
        raise OrderActionError("order item not found", status_code=404)
    unit = db.get(m.ProductUnit, oi.unit_id)
    if unit is None:
        raise OrderActionError("unit not found", status_code=404)
    order = db.get(m.Order, oi.order_id)
    if not (unit.owner_id is not None and str(unit.owner_id) == str(caller_id)):
        raise OrderActionError("you do not own this unit", status_code=403)
    if order is not None and str(order.user_id) != str(caller_id):
        raise OrderActionError("order does not belong to caller", status_code=403)
    return oi, unit, order


def record_order_item_return(
    db: Session, *, order_item_id, caller_id, reason: str, pickup_slot: str | None = None
) -> m.ReturnEvent:
    """Record an order-line return. ``wrong_item`` is fully gated."""
    oi, unit, _ = _owned_line(db, order_item_id, caller_id)
    existing = db.execute(
        select(m.ReturnEvent.id).where(m.ReturnEvent.order_item_id == oi.id)
    ).first()
    if existing is not None:
        raise OrderActionError("order item already returned", status_code=409)

    now = datetime.now(timezone.utc)

    # WRONG_ITEM → flagged return-to-seller. NO grade endpoint, NO passport /
    # GRADED anchor against the ordered unit, NO unit mutation, NO listing.
    if reason == "wrong_item":
        row = m.ReturnEvent(
            unit_id=unit.id, order_item_id=oi.id, user_id=caller_id,
            reason_code=reason, status="flagged", pickup_slot=pickup_slot,
        )
        db.add(row)
        oi.return_state = "return_to_seller"
        db.commit()
        db.refresh(row)
        return row

    # Any other reason → standard order-linked return (graded later via media).
    row = m.ReturnEvent(
        unit_id=unit.id, order_item_id=oi.id, user_id=caller_id, reason_code=reason,
        status="picked_up" if pickup_slot else "initiated",
        pickup_slot=pickup_slot, pickup_at=now if pickup_slot else None,
    )
    db.add(row)
    unit.status = "returned"
    db.add(m.LifeLedgerEvent(unit_id=unit.id, event_type="RETURN_REQUESTED"))
    if pickup_slot:
        db.add(m.LifeLedgerEvent(unit_id=unit.id, event_type="PICKED_UP"))
    db.commit()
    db.refresh(row)
    return row


def _store_pristine_passport(
    db: Session, unit: m.ProductUnit, product: m.Product | None, ret: m.ReturnEvent, now: datetime
) -> str:
    """Deterministic pristine (Grade A / Like New) passport — NO ML grade. An
    in-window exchange unit was never used, so it's pristine by construction."""
    payload = {
        "schema_version": "1.0.0", "unit_id": str(unit.id), "return_id": str(ret.id),
        "grade": settings.size_return_pristine_grade,
        "grade_numeric": settings.size_return_pristine_grade_numeric,
        "category": product.category if product else "other",
        "vertical": product.vertical if product else "fashion",
        "disposition_hint": "rescue", "defects": [], "packaging_state": "sealed",
        "confidence": 0.99, "media_hashes": [], "graded_at": now.isoformat(),
        "model_tier_used": "exchange-pristine", "warranty_months_remaining": 0,
        "repair_events": [],
    }
    digest = compute_hash(payload)
    payload["passport_hash"] = digest
    db.add(m.ConditionPassport(
        unit_id=unit.id, return_id=ret.id, passport=payload, passport_hash=digest, graded_at=now,
    ))
    return digest


def _active_rescue_listing(db: Session, unit_id) -> m.RescueListing | None:
    return db.execute(
        select(m.RescueListing)
        .where(m.RescueListing.unit_id == unit_id)
        .where(m.RescueListing.status == "active")
        .order_by(m.RescueListing.created_at.desc())
    ).scalars().first()


def exchange_order_item(
    db: Session,
    *,
    order_item_id,
    caller_id,
    new_size: str | None = None,
    new_variant: str | None = None,
    pickup_slot: str | None = None,
) -> ExchangeResult:
    """In-window exchange (NO ML grading). Mint a replacement line, mark the
    returned unit pristine, auto-list it on Path-A rescue at original/minimal
    discount (pickup-anchored), and anchor an EXCHANGED LifeLedger event."""
    oi, unit, order = _owned_line(db, order_item_id, caller_id)
    product = db.get(m.Product, oi.product_id)
    if product is None:
        raise OrderActionError("product not found", status_code=404)

    window = order_item_window(db, oi, order.user_id if order else caller_id)
    if window["returned"] or oi.return_state == "exchanged":
        raise OrderActionError("order item already returned/exchanged", status_code=409)
    if window["listed"]:
        raise OrderActionError("item is already listed for resale", status_code=409)
    # Exchange is only offered inside the return window.
    deadline = window["days_to_return_deadline"]
    if deadline is None or deadline < 0:
        raise OrderActionError("exchange only allowed within the return window", status_code=409)

    now = datetime.now(timezone.utc)
    ledger = get_ledger_client()

    # 1) Replacement order + line (new size/variant on the same product).
    replacement_order = m.Order(user_id=caller_id, status="placed", subtotal=float(product.price))
    db.add(replacement_order)
    db.flush()
    new_unit = m.ProductUnit(
        product_id=product.id, owner_id=caller_id, status="sold",
        serial=f"EXC-{str(replacement_order.id)[-6:]}-{str(product.id)[-4:]}",
        size=new_size or oi.size, geo_lat=unit.geo_lat, geo_lng=unit.geo_lng, transfer_count=0,
    )
    db.add(new_unit)
    db.flush()
    replacement_oi = m.OrderItem(
        order_id=replacement_order.id, product_id=product.id, unit_id=new_unit.id,
        sku=oi.sku, size=new_size or oi.size, variant=new_variant or oi.variant,
        qty=1, price=product.price, status="delivered", delivered_at=now,
        exchanged_from_id=oi.id,
    )
    db.add(replacement_oi)
    db.flush()
    rep_anchor = ledger.anchor(unit_id=str(new_unit.id), passport_hash="")
    db.add(m.LifeLedgerEvent(unit_id=new_unit.id, event_type="PURCHASED", tx_hash=rep_anchor.tx_hash))

    # 2) Returned unit → pristine (no ML grade), pickup pending.
    unit.status = "returned"
    oi.return_state = "exchanged"
    ret = m.ReturnEvent(
        unit_id=unit.id, order_item_id=oi.id, user_id=caller_id, reason_code="exchange",
        status="exchanged", pickup_slot=pickup_slot, pickup_at=None,
    )
    db.add(ret)
    db.flush()
    db.add(m.LifeLedgerEvent(unit_id=unit.id, event_type="RETURN_REQUESTED"))
    digest = _store_pristine_passport(db, unit, product, ret, now)
    ex_anchor = ledger.anchor(unit_id=str(unit.id), passport_hash=digest)
    db.add(m.LifeLedgerEvent(
        unit_id=unit.id, event_type="EXCHANGED", passport_hash=digest, tx_hash=ex_anchor.tx_hash,
    ))

    # 3) Auto-list the pristine unit on Path-A rescue at a minimal discount
    #    (near original price), pickup-anchored, so a local buyer can intercept
    #    it before pickup → cuts the warehouse round-trip emissions.
    listing = create_listing_for_disposition(
        db, unit=unit, channel="rescue", anchored_at=now, has_local_demand=True,
        discount_pct=settings.exchange_minimal_discount_pct,
    )
    if listing is None:
        listing = _active_rescue_listing(db, unit.id)

    # Impact: an exchange avoids a fresh make + warehouse restock cycle.
    co2 = net_co2_saved("exchange")
    db.add(m.ImpactEvent(user_id=caller_id, unit_id=unit.id, channel="exchange", co2_saved_kg=co2))

    db.commit()
    db.refresh(replacement_oi)
    if listing is not None:
        db.refresh(listing)

    original_price = float(product.price)
    list_price = (
        round(original_price * (1 - (listing.current_discount_pct or 0.0)), 2)
        if listing is not None else None
    )
    return ExchangeResult(
        exchange_id=str(ret.id),
        replacement=ExchangeReplacement(
            order_item_id=str(replacement_oi.id), size=replacement_oi.size,
            variant=replacement_oi.variant, title=product.title,
        ),
        rescue_listing=ExchangeRescueListing(
            id=str(listing.id) if listing is not None else "",
            list_price=list_price, title=product.title, original_price=original_price,
        ),
    )
