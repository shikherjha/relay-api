from __future__ import annotations

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
from app.services.pair_rescue import find_pairs
from app.services.rescue import (
    claim_guardrails,
    current_discount,
    early_access_until,
    has_early_access,
    is_embargoed,
    lifetime_credits,
)

router = APIRouter(prefix="/rescue", tags=["rescue"])


def _enrich_listing(db: Session, row: m.RescueListing, unit: m.ProductUnit | None) -> dict:
    title = category = vertical = reason = grade = None
    original_price = None
    if unit is not None:
        product = db.get(m.Product, unit.product_id)
        if product is not None:
            title = product.title
            category = product.category
            vertical = product.vertical
            original_price = float(product.price)
        ret = db.execute(
            select(m.ReturnEvent)
            .where(m.ReturnEvent.unit_id == unit.id)
            .order_by(m.ReturnEvent.created_at.desc())
        ).scalars().first()
        if ret is not None:
            reason = ret.reason_code.replace("_", " ")
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
        "original_price": original_price,
        "grade": grade,
        "reason": reason,
        "max_discount_pct": settings.rescue_discount_max,
    }


def _to_listing(
    db: Session,
    row: m.RescueListing,
    unit: m.ProductUnit | None,
    distance_km: float | None,
    early_access: bool = False,
) -> RescueListing:
    discount = current_discount(row)
    extra = _enrich_listing(db, row, unit)
    return RescueListing(
        id=str(row.id), unit_id=str(row.unit_id),
        discount_pct=discount,
        base_discount_pct=row.base_discount_pct,
        current_discount_pct=discount,
        ttl_seconds=row.ttl_seconds, expires_at=row.expires_at,
        status=row.status, claimed_by=str(row.claimed_by) if row.claimed_by else None,
        distance_km=round(distance_km, 2) if distance_km is not None else None,
        early_access=early_access,
        early_access_until=early_access_until(row) if early_access else None,
        **extra,
    )


@router.get("/feed", response_model=list[RescueListing])
def rescue_feed(
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = Query(default=15.0),
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> list[RescueListing]:
    # Pillar 5: green credits buy early access. High-credit users see new
    # listings during the embargo window; everyone else waits for them to go
    # public. The filter is keyed on the caller's lifetime credit tier.
    early = has_early_access(lifetime_credits(db, to_uuid(user_id)))

    rows = db.execute(
        select(m.RescueListing).where(m.RescueListing.status == "active")
    ).scalars().all()

    out: list[RescueListing] = []
    for row in rows:
        embargoed = is_embargoed(row)
        if embargoed and not early:
            continue  # still in its early-access window; public can't see it yet
        unit = db.get(m.ProductUnit, row.unit_id)
        distance = None
        if unit and unit.geo_lat is not None:
            distance = haversine_km(lat, lng, unit.geo_lat, unit.geo_lng)
            if distance > radius_km:
                continue
        out.append(_to_listing(db, row, unit, distance, early_access=embargoed))
    out.sort(key=lambda r: (r.distance_km if r.distance_km is not None else 1e9))
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
    db.commit()
    db.refresh(row)
    return RescueClaimResult(listing=_to_listing(db, row, db.get(m.ProductUnit, row.unit_id), None), claimed=True, guardrails_applied=[])
