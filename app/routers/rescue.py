from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.deps import current_user_id
from app.schemas.rescue import RescueClaimResult, RescueListing

router = APIRouter(prefix="/rescue", tags=["rescue"])


@router.get("/feed", response_model=list[RescueListing])
def rescue_feed(
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = Query(default=15.0),
) -> list[RescueListing]:
    # Step 3-4: engine match/rescue + decay pricing recompute on read.
    return []


@router.post("/{listing_id}/claim", response_model=RescueClaimResult)
def claim_rescue(listing_id: str, user_id: str = Depends(current_user_id)) -> RescueClaimResult:
    # Step 4: enforce guardrails (eligibility, one-active, chain cap, net-carbon).
    listing = RescueListing(id=listing_id, unit_id="stub", discount_pct=0.15, status="active")
    return RescueClaimResult(listing=listing, claimed=False, guardrails_applied=[])
