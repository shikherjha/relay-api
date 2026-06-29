from __future__ import annotations

from pydantic import BaseModel, Field


class CartItemCreate(BaseModel):
    product_id: str
    sku: str | None = None
    size: str | None = None
    variant: str | None = None
    qty: int = Field(default=1, ge=1)
    # Who this line is for (Fit Profile id). None / "self" by default.
    profile_id: str | None = None


class CartItem(CartItemCreate):
    id: str


class CartItemPatch(BaseModel):
    """Reassign a cart line — change the size or who it's for."""

    size: str | None = None
    profile_id: str | None = None
    # Distinguish "set profile to Anyone" (clear=True) from "leave unchanged".
    clear_profile: bool = False


class BracketingFlag(BaseModel):
    """Active bracketing signal — fires at ≥3 distinct size/variant of one product."""

    flagged: bool
    product_id: str
    distinct_variants: int
    sizes: list[str] = Field(default_factory=list)
    suggested_size: str | None = None
    message: str | None = None


class Cart(BaseModel):
    user_id: str
    items: list[CartItem] = Field(default_factory=list)
    bracketing: list[BracketingFlag] = Field(default_factory=list)
