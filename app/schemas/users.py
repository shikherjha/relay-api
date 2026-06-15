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


class AccessTier(BaseModel):
    name: str
    threshold: float
    early_access_hours: float = 0.0
    unlocked: bool = False


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
    # Tiered early access: each higher tier sees rescue listings earlier.
    tier: str = "standard"
    next_tier: str | None = None
    credits_to_next_tier: float | None = None
    tiers: list[AccessTier] = Field(default_factory=list)
    events: list[ImpactEventOut] = Field(default_factory=list)


class ReturnTracking(BaseModel):
    """One of the caller's returns, with live status + the resulting condition
    grade and where the item is now headed (rescue / second-life)."""

    return_id: str
    unit_id: str
    order_item_id: str | None = None
    title: str | None = None
    category: str | None = None
    vertical: str | None = None
    image_url: str | None = None
    reason_code: str | None = None
    status: str  # initiated | picked_up | graded | flagged
    created_at: datetime | None = None
    pickup_slot: str | None = None
    grade: str | None = None
    media_urls: list[str] = Field(default_factory=list)
    # Where the graded unit went next (so the buyer can follow it, even to a
    # rescue listing they can themselves claim in the demo).
    disposition_channel: str | None = None  # rescue | p2p_resale | refurb | …
    rescue_listed: bool = False
    second_life_listed: bool = False


class ResaleTracking(BaseModel):
    """One of the caller's Second-Life resale listings (p2p) with live status."""

    listing_id: str
    unit_id: str
    title: str | None = None
    category: str | None = None
    vertical: str | None = None
    image_url: str | None = None
    source: str = "p2p"
    resale_grade: str | None = None
    list_price: float | None = None
    price_min: float | None = None
    price_max: float | None = None
    status: str = "active"  # active | sold
    escrow_status: str = "none"
    age_days: int | None = None
    created_at: datetime | None = None
    media_urls: list[str] = Field(default_factory=list)
