from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import Geo


class WishlistCreate(BaseModel):
    category: str
    size: str | None = None
    max_price: float | None = None
    geo: Geo | None = None


class Wishlist(BaseModel):
    id: str
    user_id: str
    category: str
    size: str | None = None
    max_price: float | None = None
    expires_at: datetime | None = None
    wish_score: float | None = None


class WishMatch(BaseModel):
    wish_id: str
    unit_id: str
    score: float = Field(..., ge=0, le=1, description="cosine × wish_score")
    distance_km: float | None = None
