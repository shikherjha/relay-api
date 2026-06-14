"""initial schema — all core tables + pgvector

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMB_DIM = 384


def _ts(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(255)),
        sa.Column("return_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("fit_profile", postgresql.JSONB),
        sa.Column("rescue_eligible", sa.Boolean, nullable=False, server_default="true"),
        _ts(),
    )

    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sku", sa.String(64), nullable=False, index=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("category", sa.String(64), nullable=False, index=True),
        sa.Column("vertical", sa.String(32), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("metadata", postgresql.JSONB),
    )

    op.create_table(
        "product_units",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("serial", sa.String(128)),
        sa.Column("status", sa.String(32), nullable=False, server_default="in_stock"),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("transfer_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("geo_lat", sa.Float),
        sa.Column("geo_lng", sa.Float),
        sa.Column("embedding", Vector(EMB_DIM)),
    )

    op.create_table(
        "return_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reason_code", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="initiated"),
        _ts(),
    )

    op.create_table(
        "condition_passports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("return_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("return_events.id", ondelete="SET NULL")),
        sa.Column("passport", postgresql.JSONB, nullable=False),
        sa.Column("passport_hash", sa.String(64), index=True),
        _ts("graded_at"),
    )

    op.create_table(
        "reverse_wishlist",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(64), nullable=False, index=True),
        sa.Column("size", sa.String(32)),
        sa.Column("max_price", sa.Numeric(12, 2)),
        sa.Column("geo_lat", sa.Float),
        sa.Column("geo_lng", sa.Float),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("embedding", Vector(EMB_DIM)),
        sa.Column("wish_score", sa.Float),
        _ts(),
    )

    op.create_table(
        "rescue_listings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("base_discount_pct", sa.Float, nullable=False, server_default="0.15"),
        sa.Column("current_discount_pct", sa.Float, nullable=False, server_default="0.15"),
        sa.Column("ttl_seconds", sa.Integer),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("claimed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        _ts(),
    )

    op.create_table(
        "pair_rescue_matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("unit_a", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("unit_b", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_a", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("user_b", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("distance_km", sa.Float),
        sa.Column("status", sa.String(32), nullable=False, server_default="proposed"),
        _ts(),
    )

    op.create_table(
        "p2p_listings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="listed"),
        sa.Column("escrow_status", sa.String(32), nullable=False, server_default="none"),
        _ts(),
    )

    op.create_table(
        "cart_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sku", sa.String(64)),
        sa.Column("size", sa.String(32)),
        sa.Column("variant", sa.String(64)),
        sa.Column("qty", sa.Integer, nullable=False, server_default="1"),
        _ts(),
    )

    op.create_table(
        "lifeledger_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("tx_hash", sa.String(80)),
        sa.Column("passport_hash", sa.String(64)),
        _ts(),
    )

    op.create_table(
        "warranty_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("months_remaining", sa.Integer, nullable=False, server_default="0"),
        sa.Column("repair_events", postgresql.JSONB),
    )

    op.create_table(
        "green_credit_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("unlock_at", sa.DateTime(timezone=True)),
        _ts(),
    )

    op.create_table(
        "impact_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_units.id", ondelete="SET NULL")),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("co2_saved_kg", sa.Float, nullable=False, server_default="0"),
        _ts(),
    )

    # pgvector ANN indexes (cosine) for next-owner matching
    op.execute(
        "CREATE INDEX ix_product_units_embedding ON product_units "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX ix_reverse_wishlist_embedding ON reverse_wishlist "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_reverse_wishlist_embedding", table_name="reverse_wishlist")
    op.drop_index("ix_product_units_embedding", table_name="product_units")
    for table in (
        "impact_events",
        "green_credit_ledger",
        "warranty_records",
        "lifeledger_events",
        "cart_items",
        "p2p_listings",
        "pair_rescue_matches",
        "rescue_listings",
        "reverse_wishlist",
        "condition_passports",
        "return_events",
        "product_units",
        "products",
        "users",
    ):
        op.drop_table(table)
