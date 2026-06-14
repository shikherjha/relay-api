from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import current_user_id
from app.schemas.p2p import P2PListing, P2PListingCreate

router = APIRouter(prefix="/p2p", tags=["p2p"])


@router.post("/listings", response_model=P2PListing, status_code=201)
def create_listing(payload: P2PListingCreate, user_id: str = Depends(current_user_id)) -> P2PListing:
    # Step 3: persist listing seeded from a graded return.
    return P2PListing(
        id="stub",
        unit_id=payload.unit_id,
        seller_id=user_id,
        price=payload.price or 0.0,
        status="listed",
        escrow_status="none",
    )
