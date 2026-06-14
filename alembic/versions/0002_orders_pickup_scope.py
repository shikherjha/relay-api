"""orders + order-linked/pickup returns + two-path rescue scope (additive)

Revision ID: 0002_orders_pickup_scope
Revises: 0001_initial
Create Date: 2026-06-14

Purely additive: new orders/order_items tables, pickup + order-link columns on
return_events, and scope/fulfillment columns on rescue_listings. No existing
columns are dropped or retyped, so this is safe to apply on a seeded DB.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_orders_pickup_scope"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="placed"),
        sa.Column("subtotal", sa.Numeric(12, 2)),
        sa.Column("placed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_orders_user_id", "orders", ["user_id"])

    op.create_table(
        "order_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_units.id", ondelete="SET NULL")),
        sa.Column("sku", sa.String(64)),
        sa.Column("size", sa.String(32)),
        sa.Column("variant", sa.String(64)),
        sa.Column("qty", sa.Integer, nullable=False, server_default="1"),
        sa.Column("price", sa.Numeric(12, 2)),
        sa.Column("status", sa.String(32), nullable=False, server_default="delivered"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])
    op.create_index("ix_order_items_unit_id", "order_items", ["unit_id"])

    # return_events: order link + pickup-anchored TTL
    op.add_column(
        "return_events",
        sa.Column("order_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("order_items.id", ondelete="SET NULL")),
    )
    op.add_column("return_events", sa.Column("pickup_slot", sa.String(64)))
    op.add_column("return_events", sa.Column("pickup_at", sa.DateTime(timezone=True)))

    # rescue_listings: two-path disposition (local vs national)
    op.add_column(
        "rescue_listings",
        sa.Column("scope", sa.String(16), nullable=False, server_default="local"),
    )
    op.add_column("rescue_listings", sa.Column("fulfillment", sa.String(32)))


def downgrade() -> None:
    op.drop_column("rescue_listings", "fulfillment")
    op.drop_column("rescue_listings", "scope")
    op.drop_column("return_events", "pickup_at")
    op.drop_column("return_events", "pickup_slot")
    op.drop_column("return_events", "order_item_id")
    op.drop_index("ix_order_items_unit_id", table_name="order_items")
    op.drop_index("ix_order_items_order_id", table_name="order_items")
    op.drop_table("order_items")
    op.drop_index("ix_orders_user_id", table_name="orders")
    op.drop_table("orders")
