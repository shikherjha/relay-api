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
from app.schemas.resale import PriceRange
from app.schemas.wishlist import WishMatch

CANDIDATE_STATUSES = ("returned", "graded", "in_stock")

# Which fit-profile axis covers a category (everything else → "tops").
_FIT_AXIS = {
    "jeans": "bottoms", "pants": "bottoms", "trousers": "bottoms",
    "shorts": "bottoms", "skirt": "bottoms",
    "sneakers": "shoes", "shoes": "shoes", "footwear": "shoes",
}


def _fit_confidence(user: m.User | None, category: str | None) -> float:
    """Confidence we know the wisher's size for this category's axis. A stored
    fit profile for that axis (tops/bottoms/shoes) grants high confidence so we
    can trust an inexact-size match; otherwise 0 (require an exact size match)."""
    fp = getattr(user, "fit_profile", None)
    if not isinstance(fp, dict) or not fp:
        return 0.0
    axis = _FIT_AXIS.get((category or "").lower(), "tops")
    return settings.matching_fit_profile_confidence if fp.get(axis) else 0.0


def _national_listings(db: Session) -> dict:
    """unit_id -> active national (Path B) rescue listing, for shipped matches."""
    rows = db.execute(
        select(m.RescueListing)
        .where(m.RescueListing.status == "active")
        .where(m.RescueListing.scope == "national")
    ).scalars().all()
    return {row.unit_id: row for row in rows}


def _enrich(db: Session, unit: m.ProductUnit) -> tuple[str | None, str | None, str | None, str | None]:
    """(title, category, vertical, grade) for a matched unit."""
    product = db.get(m.Product, unit.product_id)
    title = product.title if product else None
    category = product.category if product else None
    vertical = product.vertical if product else None
    grade = None
    passport = db.execute(
        select(m.ConditionPassport)
        .where(m.ConditionPassport.unit_id == unit.id)
        .order_by(m.ConditionPassport.graded_at.desc())
    ).scalars().first()
    if passport is not None and isinstance(passport.passport, dict):
        grade = passport.passport.get("grade")
    return title, category, vertical, grade


def _matches_for_wish(
    db: Session, wish: m.ReverseWishlist, limit: int, national: dict
) -> list[WishMatch]:
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

    # Size-match GATE (PRD §"size match OR fit confidence > 0.7"): when the wish
    # specifies a size, a candidate only passes if its size equals the wish size
    # OR the wisher has a confident fit profile for that axis.
    wisher = db.get(m.User, wish.user_id)
    fit_conf = _fit_confidence(wisher, wish.category)
    size_gate = bool(wish.size)

    out: list[WishMatch] = []
    radius = settings.rescue_default_radius_km * 5  # matching is wider than rescue
    for unit, price, sim in scored:
        if wish.max_price is not None and price is not None and float(price) > float(wish.max_price):
            continue
        if size_gate and fit_conf <= settings.matching_fit_confidence_threshold:
            unit_size = (unit.size or "").strip().lower()
            if unit_size != str(wish.size).strip().lower():
                continue  # wrong size and no fit confidence → filtered
        nat = national.get(unit.id)
        distance = None
        if wish.geo_lat is not None and unit.geo_lat is not None:
            distance = haversine_km(wish.geo_lat, wish.geo_lng, unit.geo_lat, unit.geo_lng)
            # National (Path B) units ship anywhere → no distance gate.
            if nat is None and distance > radius:
                continue
        score = max(0.0, min(1.0, sim)) * (wish.wish_score or 0.5)
        title, category, vertical, grade = _enrich(db, unit)

        # Offer price: discounted for national (Path B) rescue relists, else the
        # catalogue price. price_fit = the wish budget sits within ~15% above it.
        price_f = float(price) if price is not None else None
        list_price = price_f
        price_range = None
        if price_f is not None:
            if nat is not None:
                list_price = round(price_f * (1 - (nat.current_discount_pct or 0.0)), 2)
                lo = round(price_f * (1 - settings.rescue_discount_max), 2)
                hi = round(price_f * (1 - (nat.base_discount_pct or settings.rescue_discount_base)), 2)
                price_range = PriceRange(min=min(lo, hi), max=max(lo, hi))
            else:
                price_range = PriceRange(min=round(price_f * 0.9, 2), max=round(price_f * 1.1, 2))

        max_price = float(wish.max_price) if wish.max_price is not None else None
        price_fit = (
            max_price is not None and list_price is not None
            and list_price <= max_price <= list_price * (1 + settings.price_fit_band)
        )

        out.append(WishMatch(
            wish_id=str(wish.id), unit_id=str(unit.id),
            score=round(score, 4),
            distance_km=round(distance, 2) if distance is not None else None,
            title=title, category=category, vertical=vertical, size=unit.size, grade=grade,
            price=price_f,
            scope="national" if nat is not None else "local",
            fulfillment="shipped" if nat is not None else "local_pickup",
            listing_id=str(nat.id) if nat is not None else None,
            discount_pct=nat.current_discount_pct if nat is not None else None,
            list_price=list_price,
            price_range=price_range,
            price_fit=price_fit,
        ))
    out.sort(key=lambda x: x.score, reverse=True)
    return out[:limit]


def match_for_user(db: Session, user_id, limit: int = 5) -> list[WishMatch]:
    wishes = db.execute(
        select(m.ReverseWishlist).where(m.ReverseWishlist.user_id == user_id)
    ).scalars().all()
    national = _national_listings(db)
    out: list[WishMatch] = []
    for wish in wishes:
        out.extend(_matches_for_wish(db, wish, limit, national))
    out.sort(key=lambda x: x.score, reverse=True)
    return out
