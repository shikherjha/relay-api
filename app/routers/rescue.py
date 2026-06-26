from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import current_user_id
from app.core.geo import haversine_km
from app.core.ids import to_uuid
from app.db.session import get_db
from app.models import entities as m
from app.schemas.rescue import PairMatch, RescueClaimResult, RescueListing
from app.schemas.resale import PriceRange
from app.services.pair_rescue import find_pairs
from app.services.rescue import (
    claim_guardrails,
    current_discount,
    early_access_until,
    is_embargoed,
    lifetime_credits,
    user_lead_seconds,
    visible_to,
)

router = APIRouter(prefix="/rescue", tags=["rescue"])

RESCUE_CHANNEL = "rescue"


def _award_rescue_credits(db: Session, *, user_id, unit_id) -> None:
    """Green credits (immediately spendable) + impact event for a rescue claim."""
    from app.core.carbon import credits_for_co2, net_co2_saved

    co2 = net_co2_saved(RESCUE_CHANNEL)
    db.add(m.ImpactEvent(user_id=user_id, unit_id=unit_id, channel=RESCUE_CHANNEL, co2_saved_kg=co2))
    credits = credits_for_co2(co2)
    if credits > 0:
        db.add(m.GreenCreditLedger(
            user_id=user_id, amount=credits, reason=f"rescue_claim:{RESCUE_CHANNEL}",
            unlock_at=None,  # immediately spendable
        ))


def _enrich_listing(db: Session, row: m.RescueListing, unit: m.ProductUnit | None) -> dict:
    title = category = vertical = reason = grade = image_url = None
    original_price = None
    returned_at = None
    if unit is not None:
        product = db.get(m.Product, unit.product_id)
        if product is not None:
            title = product.title
            category = product.category
            vertical = product.vertical
            image_url = product.image_url
            original_price = float(product.price)
        ret = db.execute(
            select(m.ReturnEvent)
            .where(m.ReturnEvent.unit_id == unit.id)
            .order_by(m.ReturnEvent.created_at.desc())
        ).scalars().first()
        if ret is not None:
            reason = ret.reason_code.replace("_", " ")
            returned_at = ret.created_at
        passport = db.execute(
            select(m.ConditionPassport)
            .where(m.ConditionPassport.unit_id == unit.id)
            .order_by(m.ConditionPassport.graded_at.desc())
        ).scalars().first()
        if passport is not None and isinstance(passport.passport, dict):
            grade = passport.passport.get("grade")
    return {
        "title": title,
        "category": category,
        "vertical": vertical,
        "image_url": image_url,
        "original_price": original_price,
        "grade": grade,
        "reason": reason,
        "returned_at": returned_at,
        "max_discount_pct": settings.rescue_discount_max,
    }


def _price_band(original_price: float | None, current_discount_pct: float, base_discount_pct: float | None) -> tuple[float | None, PriceRange | None]:
    """list_price = price at the current (decayed) discount; the band runs from
    the deepest possible discount (max decay) up to the base discount."""
    if original_price is None:
        return None, None
    base = base_discount_pct if base_discount_pct is not None else settings.rescue_discount_base
    list_price = round(original_price * (1 - current_discount_pct), 2)
    floor = round(original_price * (1 - settings.rescue_discount_max), 2)  # deepest discount
    ceiling = round(original_price * (1 - base), 2)  # shallowest (base) discount
    return list_price, PriceRange(min=min(floor, ceiling), max=max(floor, ceiling))


def _to_listing(
    db: Session,
    row: m.RescueListing,
    unit: m.ProductUnit | None,
    distance_km: float | None,
    early_access: bool = False,
) -> RescueListing:
    discount = current_discount(row)
    extra = _enrich_listing(db, row, unit)
    scope = row.scope or "local"
    list_price, price_range = _price_band(extra["original_price"], discount, row.base_discount_pct)
    return RescueListing(
        id=str(row.id), unit_id=str(row.unit_id),
        discount_pct=discount,
        base_discount_pct=row.base_discount_pct,
        current_discount_pct=discount,
        ttl_seconds=row.ttl_seconds, expires_at=row.expires_at,
        status=row.status, claimed_by=str(row.claimed_by) if row.claimed_by else None,
        distance_km=round(distance_km, 2) if distance_km is not None else None,
        scope=scope,
        ships=scope == "national",
        fulfillment=row.fulfillment or ("shipped" if scope == "national" else "local_pickup"),
        pickup_anchored=scope == "local" and row.ttl_seconds is not None,
        early_access=early_access,
        early_access_until=early_access_until(row) if early_access else None,
        list_price=list_price,
        price_range=price_range,
        **extra,
    )


@router.get("/feed", response_model=list[RescueListing])
def rescue_feed(
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = Query(default=15.0),
    scope: str = Query(default="local", pattern="^(local|national|all)$"),
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> list[RescueListing]:
    # Pillar 5 (tiered): green credits buy early access. The caller's lifetime
    # credit tier grants a "lead" — how early before a listing goes public it can
    # be seen. Gold sees from creation, silver later, standard only when public.
    viewer = to_uuid(user_id)
    lead = user_lead_seconds(lifetime_credits(db, viewer))

    # A user always sees listings for items THEY returned — the early-access
    # embargo gives high-tier users a head-start on *others'* drops, it must never
    # hide your own just-returned item from you (it should land on top instantly).
    my_returned_units = set(
        db.execute(
            select(m.ReturnEvent.unit_id).where(m.ReturnEvent.user_id == viewer)
        ).scalars().all()
    )

    rows = db.execute(
        select(m.RescueListing).where(m.RescueListing.status == "active")
    ).scalars().all()

    out: list[RescueListing] = []
    for row in rows:
        row_scope = row.scope or "local"
        if scope != "all" and row_scope != scope:
            continue
        owned = row.unit_id in my_returned_units
        if not owned and not visible_to(row, lead):
            continue  # still embargoed for this tier (but never for the returner)
        embargoed = is_embargoed(row)
        unit = db.get(m.ProductUnit, row.unit_id)
        distance = None
        if row_scope == "local":
            # Hyperlocal intercept is distance-gated; national (Path B) ships.
            if unit and unit.geo_lat is not None:
                distance = haversine_km(lat, lng, unit.geo_lat, unit.geo_lng)
                if distance > radius_km and not owned:
                    continue
        out.append(_to_listing(db, row, unit, distance, early_access=embargoed))
    # Most-recently-returned first across all scopes — a just-returned unit (e.g.
    # the MacBook) lands on top whether it's a local pickup or a national relist.
    # `returned_at` is the true return time; listings with no return context sort
    # last (epoch).
    _epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

    def _returned_key(r: RescueListing) -> float:
        ra = r.returned_at
        if ra is None:
            return _epoch.timestamp()
        return (ra if ra.tzinfo is not None else ra.replace(tzinfo=timezone.utc)).timestamp()

    out.sort(key=_returned_key, reverse=True)
    return out


@router.get("/pair-matches", response_model=list[PairMatch])
def pair_matches(
    radius_km: float = Query(default=15.0),
    db: Session = Depends(get_db),
) -> list[PairMatch]:
    return find_pairs(db, radius_km=radius_km)


@router.post("/{listing_id}/claim", response_model=RescueClaimResult)
def claim_rescue(
    listing_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> RescueClaimResult:
    row = db.get(m.RescueListing, to_uuid(listing_id, what="listing id"))
    if row is None:
        raise HTTPException(status_code=404, detail="listing not found")

    user = db.get(m.User, to_uuid(user_id))
    guardrails = claim_guardrails(db, row, user)
    if guardrails:
        return RescueClaimResult(listing=_to_listing(db, row, db.get(m.ProductUnit, row.unit_id), None), claimed=False, guardrails_applied=guardrails)

    row.status = "claimed"
    row.claimed_by = to_uuid(user_id)
    row.current_discount_pct = current_discount(row)
    db.add(m.LifeLedgerEvent(unit_id=row.unit_id, event_type="RESCUED"))
    # Rescuing keeps a unit in the loop → reward the rescuer with green credits
    # (immediately spendable) + an impact event, so the wallet reflects it now.
    _award_rescue_credits(db, user_id=to_uuid(user_id), unit_id=row.unit_id)
    db.commit()
    db.refresh(row)
    return RescueClaimResult(listing=_to_listing(db, row, db.get(m.ProductUnit, row.unit_id), None), claimed=True, guardrails_applied=[])
