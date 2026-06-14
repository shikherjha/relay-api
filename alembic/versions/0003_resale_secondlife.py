"""Track B "Second Life" resale listings + delivery/return-window + product image

Revision ID: 0003_resale_secondlife
Revises: 0002_orders_pickup_scope
Create Date: 2026-06-14

Purely additive: a new `resale_listings` table (buyer p2p resells + seller
certified republishes), `order_items.delivered_at` (anchors the return/resell
window clock), and `products.image_url` (catalogue photo + grading input).
No existing columns are dropped or retyped, so this applies cleanly on the
already-seeded DB at head 0002.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_resale_secondlife"
down_revision: Union[str, None] = "0002_orders_pickup_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # products: catalogue image (also the input image for return/resell grading)
    op.add_column("products", sa.Column("image_url", sa.String(512)))

    # order_items: delivery timestamp anchors the return-window / resell clock
    op.add_column(
        "order_items",
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
    )

    # resale_listings: the Second Life catalogue (p2p resells + certified relists)
    op.create_table(
        "resale_listings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lister_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("source", sa.String(16), nullable=False, server_default="p2p"),
        sa.Column("original_price", sa.Numeric(12, 2)),
        sa.Column("price_min", sa.Numeric(12, 2)),
        sa.Column("price_max", sa.Numeric(12, 2)),
        sa.Column("list_price", sa.Numeric(12, 2)),
        sa.Column("resale_grade", sa.String(8)),
        sa.Column("age_days", sa.Integer),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("escrow_status", sa.String(32), nullable=False, server_default="none"),
        sa.Column("sold_to", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_resale_listings_unit_id", "resale_listings", ["unit_id"])
    op.create_index("ix_resale_listings_source", "resale_listings", ["source"])
    op.create_index("ix_resale_listings_status", "resale_listings", ["status"])


def downgrade() -> None:
    op.drop_index("ix_resale_listings_status", table_name="resale_listings")
    op.drop_index("ix_resale_listings_source", table_name="resale_listings")
    op.drop_index("ix_resale_listings_unit_id", table_name="resale_listings")
    op.drop_table("resale_listings")
    op.drop_column("order_items", "delivered_at")
    op.drop_column("products", "image_url")
