"""Track B "Second Life" resale/republish schemas.

A buyer can re-list a unit they own once its return window has expired (source
"p2p"), and a seller can republish a refurbished unit they got back (source
"certified"). Both land in the same Second Life catalogue (`resale_listings`).

`ResaleAssessment` is the grade-and-price result returned by the ML boundary
(`grade_and_price`): a real Condition Passport plus a derived resale price band.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.ml import ConditionPassport, Verification

ResaleSource = Literal["p2p", "certified"]
ResaleStatus = Literal["active", "sold", "cancelled"]
EscrowStatus = Literal["none", "held", "released", "refunded"]


class PriceRange(BaseModel):
    min: float
    max: float


class ResaleAssessment(BaseModel):
    """grade_and_price output: Bedrock/mock grade + derived resale pricing."""

    passport: ConditionPassport
    resale_grade: str
    grade_numeric: float = Field(..., ge=0, le=1)
    original_price: float
    age_days: int
    price_range: PriceRange
    list_price: float
    # Human-readable pricing rationale (relay-ml /grade-and-price; optional).
    pricing_rationale: str | None = None
    # "ml" = priced by relay-ml /grade-and-price; "fallback" = real/mock grade +
    # deterministic local pricer (Bhavya's endpoint unavailable).
    source: Literal["ml", "fallback"] = "fallback"


class ResaleListing(BaseModel):
    id: str
    unit_id: str
    source: ResaleSource
    title: str | None = None
    category: str | None = None
    vertical: str | None = None
    # Catalogue image (absolute S3 URL): derived from the unit's product/order.
    image_url: str | None = None
    # Absolute S3 URLs of the reseller-uploaded photos/video (buyer or seller).
    media_urls: list[str] = Field(default_factory=list)
    resale_grade: str | None = None
    pricing_rationale: str | None = None
    original_price: float | None = None
    price_range: PriceRange
    list_price: float
    age_days: int | None = None
    lister_label: str | None = None
    ships: bool = False
    fulfillment: str | None = None  # "shipped" | "local_pickup"
    status: ResaleStatus = "active"
    escrow_status: EscrowStatus = "none"
    passport_id: str | None = None
    lifeledger_unit_id: str | None = None
    # Additive AI order-vs-item verification surfaced from the unit's passport.
    verification: Verification | None = None


class BuyResult(BaseModel):
    ok: bool = True
    listing_id: str
    escrow_status: EscrowStatus = "released"
    new_owner_id: str
    tx_hash: str


class SellerRefurbUnit(BaseModel):
    unit_id: str
    title: str | None = None
    category: str | None = None
    vertical: str | None = None
    image_url: str | None = None
    original_price: float | None = None
    age_days: int | None = None
    last_event: str | None = None
    grade: str | None = None


# Broad seller order-history line (every sold unit, not just the relist-eligible
# subset surfaced by /seller/refurbished). `status` reflects the unit/ledger
# reality; `relistable` matches /seller/refurbished eligibility exactly.
SellerOrderStatus = Literal["delivered", "returned", "refurbished", "relisted", "sold"]


class SellerOrderItem(BaseModel):
    order_id: str
    order_item_id: str
    unit_id: str | None = None
    title: str | None = None
    category: str | None = None
    vertical: str | None = None
    image_url: str | None = None
    sale_price: float | None = None
    sold_at: datetime | None = None
    delivered_at: datetime | None = None
    buyer_label: str | None = None
    status: SellerOrderStatus = "delivered"
    relistable: bool = False
    listing_id: str | None = None
    age_days: int | None = None
    last_event: str | None = None
