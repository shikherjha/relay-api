from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

RescueStatus = Literal["active", "claimed", "expired", "cancelled"]


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
    # Enriched for UI (product + passport context).
    title: str | None = None
    category: str | None = None
    vertical: str | None = None
    original_price: float | None = None
    grade: str | None = None
    reason: str | None = None
    max_discount_pct: float | None = None
    # Pillar 5: true when this listing is still inside its early-access embargo
    # window — only high-credit users see it before `early_access_until`.
    early_access: bool = False
    early_access_until: datetime | None = None


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
