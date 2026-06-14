"""SQLAlchemy models mirroring plan.md §6 data model.

Schema-first: these define the persistent shape the API endpoints and engine
build on. Embeddings use pgvector (384-d). UUID PKs default server-side via
pgcrypto's gen_random_uuid().
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.db.base import Base

EMB_DIM = settings.embedding_dim


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    return_rate: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    fit_profile: Mapped[dict | None] = mapped_column(JSONB)
    rescue_eligible: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = _created_at()


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = _uuid_pk()
    sku: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    category: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    vertical: Mapped[str] = mapped_column(String(32), nullable=False)  # fashion | electronics
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(512))
    product_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB)


class ProductUnit(Base):
    __tablename__ = "product_units"

    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    serial: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="in_stock", server_default="in_stock")
    # Physical size of this unit (mirrors the order line that sold it). Drives
    # the next-owner size-match gate (matching.py).
    size: Mapped[str | None] = mapped_column(String(32))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    transfer_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    geo_lat: Mapped[float | None] = mapped_column(Float)
    geo_lng: Mapped[float | None] = mapped_column(Float)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMB_DIM))


class Order(Base):
    """A Layer-1 (Amazon) checkout. Source of truth for order history + returns."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32), default="placed", server_default="placed")
    subtotal: Mapped[float | None] = mapped_column(Numeric(12, 2))
    placed_at: Mapped[datetime] = _created_at()


class OrderItem(Base):
    """One purchased line, bound to a physical unit so returns act on real goods."""

    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_units.id", ondelete="SET NULL")
    )
    sku: Mapped[str | None] = mapped_column(String(64))
    size: Mapped[str | None] = mapped_column(String(32))
    variant: Mapped[str | None] = mapped_column(String(64))
    qty: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(32), default="delivered", server_default="delivered")
    # When the line was delivered → anchors the return-window / resell clock.
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Post-return disposition state for the line: e.g. "return_to_seller"
    # (wrong_item flagged) or "exchanged". None for normal lines.
    return_state: Mapped[str | None] = mapped_column(String(32))
    # Replacement lines created by an exchange point back to the original line.
    exchanged_from_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("order_items.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()


class ReturnEvent(Base):
    __tablename__ = "return_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_units.id", ondelete="CASCADE"))
    # Order-linked returns: bind a return to the exact purchased line item.
    order_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("order_items.id", ondelete="SET NULL")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="initiated", server_default="initiated")
    # Pickup-anchored reverse logistics: the rescue TTL clock starts at pickup_at.
    pickup_slot: Mapped[str | None] = mapped_column(String(64))
    pickup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class ConditionPassport(Base):
    __tablename__ = "condition_passports"

    id: Mapped[uuid.UUID] = _uuid_pk()
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_units.id", ondelete="CASCADE"))
    return_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("return_events.id", ondelete="SET NULL")
    )
    passport: Mapped[dict] = mapped_column(JSONB, nullable=False)
    passport_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    graded_at: Mapped[datetime] = _created_at()


class ReverseWishlist(Base):
    __tablename__ = "reverse_wishlist"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    size: Mapped[str | None] = mapped_column(String(32))
    max_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    geo_lat: Mapped[float | None] = mapped_column(Float)
    geo_lng: Mapped[float | None] = mapped_column(Float)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMB_DIM))
    wish_score: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = _created_at()


class RescueListing(Base):
    __tablename__ = "rescue_listings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_units.id", ondelete="CASCADE"))
    base_discount_pct: Mapped[float] = mapped_column(Float, default=0.15, server_default="0.15")
    current_discount_pct: Mapped[float] = mapped_column(Float, default=0.15, server_default="0.15")
    ttl_seconds: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active")
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Two-path disposition. Path A (local): pickup-anchored, local pickup/courier.
    # Path B (national): warehouse Certified Second-Life relist, shipped, no decay.
    scope: Mapped[str] = mapped_column(String(16), default="local", server_default="local")
    fulfillment: Mapped[str | None] = mapped_column(String(32))  # local_pickup|courier|shipped
    created_at: Mapped[datetime] = _created_at()


class PairRescueMatch(Base):
    __tablename__ = "pair_rescue_matches"

    id: Mapped[uuid.UUID] = _uuid_pk()
    unit_a: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_units.id", ondelete="CASCADE"))
    unit_b: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_units.id", ondelete="CASCADE"))
    user_a: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    user_b: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    distance_km: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="proposed", server_default="proposed")
    created_at: Mapped[datetime] = _created_at()


class P2PListing(Base):
    __tablename__ = "p2p_listings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_units.id", ondelete="CASCADE"))
    seller_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="listed", server_default="listed")
    escrow_status: Mapped[str] = mapped_column(String(32), default="none", server_default="none")
    created_at: Mapped[datetime] = _created_at()


class ResaleListing(Base):
    """Track B "Second Life" listing: a buyer re-listing a unit they own
    (source "p2p") or a seller republishing a refurbished unit (source
    "certified"). Carries the resale grade + AI-derived price band."""

    __tablename__ = "resale_listings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_units.id", ondelete="CASCADE"))
    lister_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(16), default="p2p", server_default="p2p")
    original_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    price_min: Mapped[float | None] = mapped_column(Numeric(12, 2))
    price_max: Mapped[float | None] = mapped_column(Numeric(12, 2))
    list_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    # Widened to hold ML resale labels ("Like New", "Very Good", …) as well as
    # the local fallback's letter grade ("B+").
    resale_grade: Mapped[str | None] = mapped_column(String(32))
    age_days: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active")
    escrow_status: Mapped[str] = mapped_column(String(32), default="none", server_default="none")
    sold_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Absolute S3 URLs of the reseller-uploaded photos/video (buyer or seller).
    media_urls: Mapped[list | None] = mapped_column(JSONB)
    # Human-readable pricing rationale from relay-ml /grade-and-price (when live).
    pricing_rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class CartItem(Base):
    __tablename__ = "cart_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    sku: Mapped[str | None] = mapped_column(String(64))
    size: Mapped[str | None] = mapped_column(String(32))
    variant: Mapped[str | None] = mapped_column(String(64))
    qty: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = _created_at()


class LifeLedgerEvent(Base):
    __tablename__ = "lifeledger_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_units.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tx_hash: Mapped[str | None] = mapped_column(String(80))
    passport_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created_at()


class WarrantyRecord(Base):
    __tablename__ = "warranty_records"

    id: Mapped[uuid.UUID] = _uuid_pk()
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_units.id", ondelete="CASCADE"))
    months_remaining: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    repair_events: Mapped[dict | None] = mapped_column(JSONB)


class GreenCreditLedger(Base):
    __tablename__ = "green_credit_ledger"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    unlock_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class ImpactEvent(Base):
    __tablename__ = "impact_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product_units.id", ondelete="SET NULL"))
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    co2_saved_kg: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    created_at: Mapped[datetime] = _created_at()
