from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.dispatch import DispatchReason
from app.schemas.resale import PriceRange

RescueStatus = Literal["active", "claimed", "expired", "cancelled"]
# Path A = hyperlocal intercept (pickup-anchored TTL, local pickup/courier).
# Path B = warehouse "Certified Second-Life" relist (national, shipped).
RescueScope = Literal["local", "national"]


class RescueListing(BaseModel):
    id: str
    unit_id: str
    # contract-compatible: discount_pct mirrors current_discount_pct
    discount_pct: float
    base_discount_pct: float | None = None
    current_discount_pct: float | None = None
    ttl_seconds: int | None = None
    expires_at: datetime | None = None
    status: RescueStatus = "active"
    claimed_by: str | None = None
    distance_km: float | None = None
    # Two-path disposition: local intercept vs national certified relist.
    scope: RescueScope = "local"
    ships: bool = False
    fulfillment: str | None = None  # "local_pickup" | "courier" | "shipped"
    pickup_anchored: bool = False
    # Enriched for UI (product + passport context).
    title: str | None = None
    category: str | None = None
    vertical: str | None = None
    image_url: str | None = None
    original_price: float | None = None
    grade: str | None = None
    reason: str | None = None
    # When the unit was most recently returned — drives "newest return on top".
    returned_at: datetime | None = None
    max_discount_pct: float | None = None
    # Price the buyer actually pays now (decayed) + the band it can move within.
    list_price: float | None = None
    price_range: PriceRange | None = None
    # Pillar 5: true when this listing is still inside its early-access embargo
    # window — only high-credit users see it before `early_access_until`.
    early_access: bool = False
    early_access_until: datetime | None = None
    # Rescue Dispatch Score (§21.4) — per-viewer edge utility + the human reasons
    # it's surfaced ("Best local fit", "Matches your wish", "Clearing soon"). The
    # feed sorts by this; None until scored.
    dispatch_score: float | None = None
    dispatch_reasons: list[DispatchReason] = Field(default_factory=list)


class RescueClaimResult(BaseModel):
    listing: RescueListing
    claimed: bool
    guardrails_applied: list[str] = Field(default_factory=list)


class PairMatch(BaseModel):
    """A↔B circular swap: each user's returned unit satisfies the other's wish."""

    unit_a: str
    unit_b: str
    user_a: str | None = None
    user_b: str | None = None
    score: float = Field(..., ge=0, le=1)
    distance_km: float | None = None
    status: str = "proposed"
    # A pair swap is a special bipartite dispatch edge: no payment, both wishes
    # satisfied, lowest net carbon (one local leg each). Same framing as the feed.
    dispatch_score: float | None = None
    dispatch_reasons: list[DispatchReason] = Field(default_factory=list)
