"""Next-owner matching (engine-match-vector, run in relay-api per §6).

Real pgvector cosine ANN: rank candidate units against each wish embedding
(`embedding <=> wish.embedding`), then apply geo + price filters and multiply by
`wish_score`. Falls back to category match if a wish has no embedding.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.geo import haversine_km
from app.models import entities as m
from app.schemas.wishlist import WishMatch

CANDIDATE_STATUSES = ("returned", "graded", "in_stock")


def _matches_for_wish(db: Session, wish: m.ReverseWishlist, limit: int) -> list[WishMatch]:
    base = (
        select(m.ProductUnit, m.Product.price)
        .join(m.Product, m.Product.id == m.ProductUnit.product_id)
        .where(m.ProductUnit.status.in_(CANDIDATE_STATUSES))
        .where((m.ProductUnit.owner_id != wish.user_id) | (m.ProductUnit.owner_id.is_(None)))
    )

    if wish.embedding is not None:
        dist = m.ProductUnit.embedding.cosine_distance(wish.embedding)
        stmt = base.add_columns(dist.label("dist")).where(
            m.ProductUnit.embedding.isnot(None)
        ).order_by(dist).limit(limit * 3)
        rows = db.execute(stmt).all()
        scored = [(u, price, 1.0 - float(d)) for (u, price, d) in rows]
    else:
        stmt = base.where(m.Product.category == wish.category).limit(limit * 3)
        rows = db.execute(stmt).all()
        scored = [(u, price, 0.5) for (u, price) in rows]

    out: list[WishMatch] = []
    radius = settings.rescue_default_radius_km * 5  # matching is wider than rescue
    for unit, price, sim in scored:
        if wish.max_price is not None and price is not None and float(price) > float(wish.max_price):
            continue
        distance = None
        if wish.geo_lat is not None and unit.geo_lat is not None:
            distance = haversine_km(wish.geo_lat, wish.geo_lng, unit.geo_lat, unit.geo_lng)
            if distance > radius:
                continue
        score = max(0.0, min(1.0, sim)) * (wish.wish_score or 0.5)
        out.append(WishMatch(
            wish_id=str(wish.id), unit_id=str(unit.id),
            score=round(score, 4),
            distance_km=round(distance, 2) if distance is not None else None,
        ))
    out.sort(key=lambda x: x.score, reverse=True)
    return out[:limit]


def match_for_user(db: Session, user_id, limit: int = 5) -> list[WishMatch]:
    wishes = db.execute(
        select(m.ReverseWishlist).where(m.ReverseWishlist.user_id == user_id)
    ).scalars().all()
    out: list[WishMatch] = []
    for wish in wishes:
        out.extend(_matches_for_wish(db, wish, limit))
    out.sort(key=lambda x: x.score, reverse=True)
    return out
