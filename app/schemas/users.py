from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FitProfile(BaseModel):
    user_id: str
    return_rate: float = 0.0
    fit_profile: dict = Field(default_factory=dict)


class ImpactEventOut(BaseModel):
    channel: str
    co2_saved_kg: float
    created_at: datetime | None = None


class ImpactWallet(BaseModel):
    user_id: str
    total_co2_saved_kg: float = 0.0
    credits_balance: float = 0.0  # unlocked (spendable)
    locked_credits: float = 0.0  # within the 14-day keep-based hold
    # Pillar 5: credits buy ACCESS. lifetime_credits (locked + unlocked) is the
    # participation tier; >= threshold unlocks early access to the rescue feed.
    lifetime_credits: float = 0.0
    early_access: bool = False
    early_access_threshold: float = 0.0
    events: list[ImpactEventOut] = Field(default_factory=list)
