"""Order schemas (Layer-1 Amazon checkout → order history → order-linked returns).

A purchase on the Amazon (Layer-1) storefront creates an Order with OrderItems.
Each item is bound to a physical ProductUnit owned by the buyer, so a return can
only ever act on something that was actually bought (order history = source of
truth). The return wizard reads returnable order lines from here.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import Geo


class CheckoutItem(BaseModel):
    product_id: str
    sku: str | None = None
    size: str | None = None
    variant: str | None = None
    qty: int = Field(default=1, ge=1)


class CheckoutRequest(BaseModel):
    """Place an order. If `items` is omitted, the caller's current cart is used."""

    items: list[CheckoutItem] | None = None
    geo: Geo | None = None
    clear_cart: bool = True


class OrderItem(BaseModel):
    id: str
    product_id: str
    unit_id: str | None = None
    title: str | None = None
    category: str | None = None
    vertical: str | None = None
    image_url: str | None = None
    sku: str | None = None
    size: str | None = None
    variant: str | None = None
    qty: int = 1
    price: float | None = None
    image_category: str | None = None
    # Return-window state (Track B). `returnable` = within the window and not
    # already returned/listed; `resellable` = window expired AND still owned AND
    # not already returned/listed → eligible for a Second Life re-list.
    delivered_at: datetime | None = None
    returnable: bool = True
    resellable: bool = False
    days_to_return_deadline: int | None = None
    returned: bool = False
    listed: bool = False
    return_id: str | None = None
    # Post-return state when the line was flagged (e.g. wrong_item →
    # "return_to_seller") or exchanged ("exchanged"). None for normal lines.
    return_state: str | None = None
    # Set on a replacement line created by an exchange (links back to the
    # original order line it replaced).
    exchanged_from_id: str | None = None


class Order(BaseModel):
    id: str
    user_id: str
    status: str = "placed"
    subtotal: float | None = None
    placed_at: datetime | None = None
    items: list[OrderItem] = Field(default_factory=list)


class OrderItemReturnRequest(BaseModel):
    """POST /orders/items/{id}/return — order-line return. ``wrong_item`` is
    fully gated (flagged → return-to-seller, no grade/listing)."""

    reason: str
    pickup_slot: str | None = None


class ExchangeRequest(BaseModel):
    """POST /orders/items/{id}/exchange — in-window exchange (no ML grading)."""

    new_size: str | None = None
    new_variant: str | None = None
    pickup_slot: str | None = None


class ExchangeReplacement(BaseModel):
    order_item_id: str
    size: str | None = None
    variant: str | None = None
    title: str | None = None


class ExchangeRescueListing(BaseModel):
    id: str
    list_price: float | None = None
    title: str | None = None
    original_price: float | None = None


class ExchangeResult(BaseModel):
    exchange_id: str
    replacement: ExchangeReplacement
    rescue_listing: ExchangeRescueListing
