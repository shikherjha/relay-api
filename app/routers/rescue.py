from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import current_user_id
from app.core.geo import haversine_km
from app.core.ids import to_uuid
from app.db.session import get_db
from app.models import entities as m
from app.schemas.rescue import PairMatch, RescueClaimResult, RescueListing
from app.services.pair_rescue import find_pairs
from app.services.rescue import claim_guardrails, current_discount

router = APIRouter(prefix="/rescue", tags=["rescue"])


def _to_listing(row: m.RescueListing, unit: m.ProductUnit | None, distance_km: float | None) -> RescueListing:
    discount = current_discount(row)
    return RescueListing(
        id=str(row.id), unit_id=str(row.unit_id),
        discount_pct=discount,
        base_discount_pct=row.base_discount_pct,
        current_discount_pct=discount,
        ttl_seconds=row.ttl_seconds, expires_at=row.expires_at,
        status=row.status, claimed_by=str(row.claimed_by) if row.claimed_by else None,
        distance_km=round(distance_km, 2) if distance_km is not None else None,
    )


@router.get("/feed", response_model=list[RescueListing])
def rescue_feed(
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = Query(default=15.0),
    db: Session = Depends(get_db),
) -> list[RescueListing]:
    rows = db.execute(
        select(m.RescueListing).where(m.RescueListing.status == "active")
    ).scalars().all()

    out: list[RescueListing] = []
    for row in rows:
        unit = db.get(m.ProductUnit, row.unit_id)
        distance = None
        if unit and unit.geo_lat is not None:
            distance = haversine_km(lat, lng, unit.geo_lat, unit.geo_lng)
            if distance > radius_km:
                continue
        out.append(_to_listing(row, unit, distance))
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
        return RescueClaimResult(listing=_to_listing(row, None, None), claimed=False, guardrails_applied=guardrails)

    row.status = "claimed"
    row.claimed_by = to_uuid(user_id)
    row.current_discount_pct = current_discount(row)
    db.add(m.LifeLedgerEvent(unit_id=row.unit_id, event_type="RESCUED"))
    db.commit()
    db.refresh(row)
    return RescueClaimResult(listing=_to_listing(row, None, None), claimed=True, guardrails_applied=[])
