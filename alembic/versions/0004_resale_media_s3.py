"""Resale media URLs (S3) + wider resale grade + pricing rationale

Revision ID: 0004_resale_media_s3
Revises: 0003_resale_secondlife
Create Date: 2026-06-14

Purely additive on top of 0003:
* `resale_listings.media_urls` (JSONB) — absolute S3 URLs of reseller-uploaded
  photos/video (distinct from `image_url`, the product/catalogue image).
* `resale_listings.pricing_rationale` (text) — relay-ml /grade-and-price rationale.
* widen `resale_listings.resale_grade` 8 → 32 so ML labels ("Like New",
  "Very Good", "Acceptable") fit alongside the local fallback's letter grade.
No columns are dropped, so this applies cleanly on the already-seeded DB.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_resale_media_s3"
down_revision: Union[str, None] = "0003_resale_secondlife"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("resale_listings", sa.Column("media_urls", postgresql.JSONB(), nullable=True))
    op.add_column("resale_listings", sa.Column("pricing_rationale", sa.Text(), nullable=True))
    op.alter_column(
        "resale_listings", "resale_grade",
        existing_type=sa.String(length=8), type_=sa.String(length=32),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "resale_listings", "resale_grade",
        existing_type=sa.String(length=32), type_=sa.String(length=8),
        existing_nullable=True,
    )
    op.drop_column("resale_listings", "pricing_rationale")
    op.drop_column("resale_listings", "media_urls")
