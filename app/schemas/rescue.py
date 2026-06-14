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


class RescueClaimResult(BaseModel):
    listing: RescueListing
    claimed: bool
    guardrails_applied: list[str] = Field(default_factory=list)
