"""Cart line recipient: cart_items.profile_id

Revision ID: 0006_cart_item_profile
Revises: 0005_return_grading_decisions
Create Date: 2026-06-27

Purely additive (no drops / retypes → applies cleanly on the already-seeded DB).

* `cart_items.profile_id` — which Fit Profile a cart line is *for* (§21.1). NULL
  means "Anyone" (unassigned / a gift where size is unknown → scored neutral).
  Lets the cart score each line for its own recipient and, crucially, only treat
  duplicates of one product as bracketing when they're for the SAME person —
  "hoodie M for me + hoodie L for Priya" is two gifts, not a return-bracket.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_cart_item_profile"
down_revision: Union[str, None] = "0005_return_grading_decisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cart_items", sa.Column("profile_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("cart_items", "profile_id")
