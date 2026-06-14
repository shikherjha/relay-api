from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import current_user_id
from app.schemas.wishlist import Wishlist, WishlistCreate, WishMatch

router = APIRouter(prefix="/wishlist", tags=["wishlist"])


@router.post("", response_model=Wishlist, status_code=201)
def create_wish(payload: WishlistCreate, user_id: str = Depends(current_user_id)) -> Wishlist:
    # Step 3-4: persist + embed (relay-ml /embed) + wish_score (relay-ml /wish-score).
    return Wishlist(
        id="stub",
        user_id=user_id,
        category=payload.category,
        size=payload.size,
        max_price=payload.max_price,
    )


@router.get("/matches", response_model=list[WishMatch])
def wishlist_matches(user_id: str = Depends(current_user_id)) -> list[WishMatch]:
    # Step 4: pgvector cosine × wish_score ranking.
    return []
