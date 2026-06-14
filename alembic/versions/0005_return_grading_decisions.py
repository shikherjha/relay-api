"""Return-grading decisions: unit size, exchange link, wrong_item flag

Revision ID: 0005_return_grading_decisions
Revises: 0004_resale_media_s3
Create Date: 2026-06-15

Purely additive on top of 0004 (no drops / retypes → applies cleanly on the
already-seeded DB):
* `product_units.size` — physical size of the unit (mirrors the order line that
  sold it). Powers the next-owner size-match gate in matching.py.
* `order_items.return_state` — post-return disposition state for the line
  ("return_to_seller" for wrong_item flagged, "exchanged" for an exchange).
* `order_items.exchanged_from_id` — replacement lines created by an exchange
  link back to the original order line they replaced.

The order-vs-item `verification` block is persisted inside the ConditionPassport
JSON (condition_passports.passport), so it needs no dedicated column.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_return_grading_decisions"
down_revision: Union[str, None] = "0004_resale_media_s3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("product_units", sa.Column("size", sa.String(length=32), nullable=True))
    op.add_column("order_items", sa.Column("return_state", sa.String(length=32), nullable=True))
    op.add_column(
        "order_items",
        sa.Column("exchanged_from_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "order_items_exchanged_from_id_fkey",
        "order_items", "order_items",
        ["exchanged_from_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("order_items_exchanged_from_id_fkey", "order_items", type_="foreignkey")
    op.drop_column("order_items", "exchanged_from_id")
    op.drop_column("order_items", "return_state")
    op.drop_column("product_units", "size")
