from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import Geo
from app.schemas.resale import PriceRange


class WishlistCreate(BaseModel):
    category: str
    size: str | None = None
    max_price: float | None = None
    geo: Geo | None = None


class Wishlist(BaseModel):
    id: str
    user_id: str
    category: str
    size: str | None = None
    max_price: float | None = None
    expires_at: datetime | None = None
    wish_score: float | None = None


class MatchReason(BaseModel):
    """Explainable reason why a candidate matched a wish (Track D §21.3)."""
    type: str  # intent_match, price_fit, size_fit, geo_fit, freshness, condition_fit
    label: str  # human-readable reason
    score: float = Field(default=1.0, ge=0, le=1)


class WishMatch(BaseModel):
    wish_id: str
    unit_id: str
    score: float = Field(..., ge=0, le=1, description="cosine × wish_score")
    distance_km: float | None = None
    # Enriched for the Genie UI + national (Path B) matching.
    title: str | None = None
    category: str | None = None
    vertical: str | None = None
    # Real catalogue image (S3) for the match card.
    image_url: str | None = None
    # The candidate unit's size — only matches that clear the size gate surface.
    size: str | None = None
    grade: str | None = None
    price: float | None = None
    scope: str = "local"  # "local" (hyperlocal) | "national" (shipped)
    fulfillment: str | None = None
    listing_id: str | None = None
    discount_pct: float | None = None
    # Price the match is offered at + its band, and whether the wish's budget is
    # a snug "price fit" (max_price within ~15% above list_price).
    list_price: float | None = None
    price_range: PriceRange | None = None
    price_fit: bool = False
    # Track D §21.3: explainable match reasons for Genie UI.
    match_reasons: list[MatchReason] = Field(default_factory=list)
