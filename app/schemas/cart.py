from __future__ import annotations

from pydantic import BaseModel, Field


class CartItemCreate(BaseModel):
    product_id: str
    sku: str | None = None
    size: str | None = None
    variant: str | None = None
    qty: int = Field(default=1, ge=1)


class CartItem(CartItemCreate):
    id: str


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
