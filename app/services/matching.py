"""Next-owner matching (engine-match-vector, run in relay-api per §6).

Real pgvector cosine ANN: rank candidate units against each wish embedding
(`embedding <=> wish.embedding`), then apply geo + price filters and multiply by
`wish_score`. Falls back to category match if a wish has no embedding.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.geo import haversine_km
from app.core.taxonomy import category_relevance, classify_vertical
from app.models import entities as m
from app.schemas.resale import PriceRange
from app.schemas.wishlist import MatchReason, WishMatch

logger = logging.getLogger(__name__)

CANDIDATE_STATUSES = ("returned", "graded", "in_stock")
# Minimum blended relevance to surface. 0.5 lets same-category (1.0) and
# AI-confirmed matches through, but filters same-vertical-different-category
# noise (earphones for a "macbook", a tee for a "hoodie" → 0.25).
MATCH_RELEVANCE_FLOOR = 0.5

# Freshness: items returned within this window get a boost (volatile inventory).
_FRESHNESS_MAX_HOURS = 72  # 3 days — after this, freshness=1.0 (no boost)
_FRESHNESS_BOOST_MAX = 1.15  # 15% boost for very fresh items


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


def _freshness_score(db: Session, unit_id) -> float:
    """Freshness boost for recently-returned units (volatile inventory pattern).
    
    Items returned in the last few hours get up to a 15% score boost. After
    _FRESHNESS_MAX_HOURS (3 days), freshness = 1.0 (no boost, no penalty).
    This makes Genie surface fresh inventory first — matching Vinted/Mercari behavior.
    """
    from datetime import datetime, timezone
    row = db.execute(
        select(m.ReturnEvent.created_at)
        .where(m.ReturnEvent.unit_id == unit_id)
        .order_by(m.ReturnEvent.created_at.desc())
    ).first()
    if row is None:
        return 1.0
    created = row[0]
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    hours_ago = (now - created).total_seconds() / 3600
    if hours_ago >= _FRESHNESS_MAX_HOURS:
        return 1.0
    # Linear boost: freshest (0h) = _FRESHNESS_BOOST_MAX, at max hours = 1.0
    return 1.0 + (_FRESHNESS_BOOST_MAX - 1.0) * (1 - hours_ago / _FRESHNESS_MAX_HOURS)


def _build_match_reasons(
    *,
    rel: float,
    freshness: float,
    price_fit: bool,
    distance: float | None,
    grade: str | None,
    category: str | None,
    wish_category: str,
) -> list[MatchReason]:
    """Build explainable match reasons for the Genie UI (Track D §21.3)."""
    reasons: list[MatchReason] = []
    # Intent match
    if rel >= 1.0:
        reasons.append(MatchReason(type="intent_match", label=f"Exact {wish_category} match", score=rel))
    elif rel >= 0.5:
        reasons.append(MatchReason(type="intent_match", label=f"Related {category or 'item'}", score=rel))
    # Price fit
    if price_fit:
        reasons.append(MatchReason(type="price_fit", label="Within your budget", score=1.0))
    # Geo fit
    if distance is not None:
        if distance <= 3.0:
            reasons.append(MatchReason(type="geo_fit", label=f"Very close ({distance:.1f} km)", score=1.0))
        elif distance <= 10.0:
            reasons.append(MatchReason(type="geo_fit", label=f"Nearby ({distance:.1f} km)", score=0.8))
    # Freshness
    if freshness > 1.05:
        minutes_ago = max(1, int((freshness - 1.0) / (_FRESHNESS_BOOST_MAX - 1.0) * _FRESHNESS_MAX_HOURS * 60))
        if minutes_ago < 60:
            reasons.append(MatchReason(type="freshness", label=f"Returned {minutes_ago} min ago", score=round(freshness - 1.0, 2)))
        else:
            reasons.append(MatchReason(type="freshness", label=f"Returned ~{minutes_ago // 60}h ago", score=round(freshness - 1.0, 2)))
    # Condition fit
    if grade and grade in ("A+", "A", "B+"):
        reasons.append(MatchReason(type="condition_fit", label=f"Grade {grade} — excellent condition", score=1.0))
    elif grade and grade in ("B",):
        reasons.append(MatchReason(type="condition_fit", label=f"Grade {grade} — good condition", score=0.7))
    return reasons


def _candidate_pool(db: Session, wish: m.ReverseWishlist, wish_vertical: str | None, limit: int) -> list[dict]:
    """Recall stage: vertical-gated nearest units (cosine), else category match.
    Returns a richer pool (4× limit) for the rerank stage to choose from."""
    cols = (m.ProductUnit, m.Product.price, m.Product.title, m.Product.category, m.Product.image_url)
    base = (
        select(*cols)
        .join(m.Product, m.Product.id == m.ProductUnit.product_id)
        .where(m.ProductUnit.status.in_(CANDIDATE_STATUSES))
        .where((m.ProductUnit.owner_id != wish.user_id) | (m.ProductUnit.owner_id.is_(None)))
    )
    if wish_vertical is not None:
        base = base.where(m.Product.vertical == wish_vertical)

    if wish.embedding is not None:
        dist = m.ProductUnit.embedding.cosine_distance(wish.embedding)
        stmt = base.add_columns(dist.label("dist")).where(
            m.ProductUnit.embedding.isnot(None)
        ).order_by(dist).limit(limit * 4)
        return [
            {"unit": u, "price": price, "title": title, "category": cat,
             "image_url": img, "sim": 1.0 - float(d)}
            for (u, price, title, cat, img, d) in db.execute(stmt).all()
        ]
    stmt = base.where(m.Product.category == wish.category).limit(limit * 4)
    return [
        {"unit": u, "price": price, "title": title, "category": cat, "image_url": img, "sim": 0.5}
        for (u, price, title, cat, img) in db.execute(stmt).all()
    ]


def _grade_for(db: Session, unit_id) -> str | None:
    passport = db.execute(
        select(m.ConditionPassport)
        .where(m.ConditionPassport.unit_id == unit_id)
        .order_by(m.ConditionPassport.graded_at.desc())
    ).scalars().first()
    if passport is not None and isinstance(passport.passport, dict):
        return passport.passport.get("grade")
    return None


def _matches_for_wish(
    db: Session, wish: m.ReverseWishlist, limit: int, national: dict, ml
) -> list[WishMatch]:
    # Retrieve → (optionally) rerank (industry pattern). Recall = vertical-gated
    # cosine; rerank = Bedrock LLM relevance (relay-ml /match-rank). A HARD
    # category veto is applied BEFORE the rerank so a "jeans" wish can never
    # surface a jacket and a "macbook" wish never surfaces earphones — the LLM
    # may only refine ordering *within* the wish's category, never resurrect a
    # wrong-category candidate (fixes cross-category bleed).
    wish_vertical = classify_vertical(wish.category)
    pool = _candidate_pool(db, wish, wish_vertical, limit)
    if not pool:
        return []

    # Stage 0 — deterministic category gate. Only candidates that clear the
    # relevance floor on category alignment alone survive to the rerank stage.
    # This guarantees correctness regardless of how generous the LLM is, AND
    # shrinks the rerank payload (latency win for Genie).
    gated: list[tuple[dict, float]] = []
    for c in pool:
        cat_rel = category_relevance(wish.category, c["category"], c["title"])
        if cat_rel >= MATCH_RELEVANCE_FLOOR:
            gated.append((c, cat_rel))
    if not gated:
        return []

    # Stage 1 — LLM rerank, but ONLY when it can change the answer: skip the
    # (slow) Bedrock call when every surviving candidate is already an exact
    # category match (cat_rel == 1.0), since the deterministic score is final.
    # This makes the common Genie case (wish jeans → only jeans) instant.
    needs_rerank = any(cat_rel < 1.0 for _, cat_rel in gated)
    ai_scores: dict[str, float] = {}
    if needs_rerank:
        candidates = [
            {
                "unit_id": str(c["unit"].id), "title": c["title"], "category": c["category"],
                "price": float(c["price"]) if c["price"] is not None else None,
            }
            for c, _ in gated
        ]
        try:
            ai_scores = ml.match_rank(
                wish=wish.category, size=wish.size,
                max_price=float(wish.max_price) if wish.max_price is not None else None,
                candidates=candidates,
            )
        except Exception:  # noqa: BLE001 - relay-ml down / no rerank → taxonomy fallback
            logger.info("match_rank unavailable; falling back to taxonomy relevance", exc_info=True)
            ai_scores = {}

    cat_rel_by_unit = {str(c["unit"].id): cat_rel for c, cat_rel in gated}

    def _relevance(c: dict) -> float:
        cat_rel = cat_rel_by_unit.get(str(c["unit"].id), 0.0)
        if cat_rel < MATCH_RELEVANCE_FLOOR:
            return 0.0  # hard category veto — LLM cannot override this
        ai = ai_scores.get(str(c["unit"].id))
        return round(0.5 * ai + 0.5 * cat_rel, 4) if ai is not None else cat_rel

    pool = [c for c, _ in gated]
    wisher = db.get(m.User, wish.user_id)
    fit_conf = _fit_confidence(wisher, wish.category)
    size_gate = bool(wish.size)

    out: list[WishMatch] = []
    radius = settings.rescue_default_radius_km * 5  # matching is wider than rescue
    for c in pool:
        unit, price, sim = c["unit"], c["price"], c["sim"]
        rel = _relevance(c)
        if rel < MATCH_RELEVANCE_FLOOR:
            continue  # wrong category / irrelevant
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
        # Relevance is the primary signal; wish_score weights buyer intent.
        # Freshness boost: recently-returned items are more relevant in volatile
        # second-hand inventory (Vinted/Mercari pattern — stale items decay).
        freshness = _freshness_score(db, unit.id)
        score = max(0.0, min(1.0, rel * freshness)) * (wish.wish_score or 0.5)
        grade = _grade_for(db, unit.id)

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
            title=c["title"], category=c["category"], vertical=wish_vertical,
            size=unit.size, grade=grade, image_url=c["image_url"],
            price=price_f,
            scope="national" if nat is not None else "local",
            fulfillment="shipped" if nat is not None else "local_pickup",
            listing_id=str(nat.id) if nat is not None else None,
            discount_pct=nat.current_discount_pct if nat is not None else None,
            list_price=list_price,
            price_range=price_range,
            price_fit=price_fit,
            match_reasons=_build_match_reasons(
                rel=rel, freshness=freshness, price_fit=price_fit,
                distance=distance, grade=grade, category=c["category"],
                wish_category=wish.category,
            ),
        ))
    # Best relevance first; cosine breaks ties (closer in embedding space).
    out.sort(key=lambda x: x.score, reverse=True)
    return out[:limit]


def match_for_user(db: Session, user_id, limit: int = 5, ml=None) -> list[WishMatch]:
    if ml is None:
        from app.clients.ml_client import get_ml_client
        ml = get_ml_client()
    wishes = db.execute(
        select(m.ReverseWishlist).where(m.ReverseWishlist.user_id == user_id)
    ).scalars().all()
    national = _national_listings(db)
    out: list[WishMatch] = []
    for wish in wishes:
        out.extend(_matches_for_wish(db, wish, limit, national, ml))
    out.sort(key=lambda x: x.score, reverse=True)
    return out
