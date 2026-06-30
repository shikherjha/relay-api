"""Rescue Dispatch Score (plan.md §21.4) — per-viewer feed allocation.

Return Rescue becomes a local marketplace dispatch: relay-api loads each rescue
listing's signals from Postgres (open-wish demand near the unit, the *viewer's*
own wish match, distance, TTL decay, grade, transfer depth, price/size fit) and
POSTs a batch to relay-engine `POST /dispatch/score`, which returns a weighted
utility + human reasons per listing. The feed sorts by that score and a strong
wish match also buys a capped early-access lead (best-matched first, not just
highest-credit). Engine unreachable → the client falls back to the same formula.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.geo import geo_decay, haversine_km
from app.core.taxonomy import category_relevance
from app.models import entities as m
from app.schemas.dispatch import (
    DispatchCandidate,
    DispatchRequest,
    DispatchScore,
    DispatchViewer,
)
from app.schemas.disposition import DemandSignal
from app.schemas.rescue import RescueListing


def open_wishes(db: Session, user_id=None) -> list[m.ReverseWishlist]:
    """Non-expired reverse wishes (optionally for one user)."""
    now = datetime.now(timezone.utc)
    stmt = select(m.ReverseWishlist)
    if user_id is not None:
        stmt = stmt.where(m.ReverseWishlist.user_id == user_id)
    rows = db.execute(stmt).scalars().all()
    return [w for w in rows if not (w.expires_at and _aware(w.expires_at) < now)]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _cosine(a, b) -> float:
    if a is None or b is None:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def unit_wish_match(
    unit: m.ProductUnit, category: str | None, wishes: list[m.ReverseWishlist]
) -> tuple[float, m.ReverseWishlist | None]:
    """How strongly the VIEWER's own open wishes want this unit (0..1) + the best
    wish. cosine (gated by category relevance) × wish_score, best across wishes."""
    best, best_w = 0.0, None
    for w in wishes:
        if category_relevance(w.category, category, None) < 0.5:
            continue
        sim = _cosine(unit.embedding, w.embedding)
        score = max(0.0, min(1.0, sim)) * (w.wish_score or 0.5)
        if score > best:
            best, best_w = score, w
    return round(best, 4), best_w


def match_lead_bonus(vmatch: float) -> int:
    """Extra early-access lead (seconds) a strong wish match buys, on top of the
    credit tier — capped, and only above the match floor (the hybrid policy)."""
    if vmatch < settings.dispatch_wish_match_floor:
        return 0
    return int(round(settings.dispatch_early_access_lead_seconds * min(1.0, vmatch)))


def _demand_for(
    unit: m.ProductUnit, category: str | None,
    wishes_by_cat: dict[str, list[m.ReverseWishlist]], radius: float,
) -> DemandSignal:
    """Open-wish demand near a unit (mirrors disposition.build_demand_signal but
    over a precomputed wish index so the feed makes one pass, not N queries)."""
    count = 0
    score = 0.0
    nearest: float | None = None
    for w in wishes_by_cat.get(category or "", []):
        weight = 1.0
        if unit.geo_lat is not None and w.geo_lat is not None:
            dist = haversine_km(unit.geo_lat, unit.geo_lng, w.geo_lat, w.geo_lng)
            weight = geo_decay(dist, radius)
            if weight <= 0:
                continue
            nearest = dist if nearest is None else min(nearest, dist)
        count += 1
        score += (w.wish_score or 0.5) * weight
    return DemandSignal(
        open_wish_count=count, demand_score=round(score, 3),
        nearest_km=round(nearest, 3) if nearest is not None else None,
    )


def _ttl_remaining_frac(row: m.RescueListing, now: datetime) -> float | None:
    """remaining/ttl in [0,1] (None ⇒ no decay, e.g. a national relist)."""
    if not row.ttl_seconds or not row.expires_at:
        return None
    remaining = max(0.0, min((_aware(row.expires_at) - now).total_seconds(), row.ttl_seconds))
    return round(remaining / row.ttl_seconds, 4)


def _grade_numeric(db: Session, unit_id) -> float:
    passport = db.execute(
        select(m.ConditionPassport)
        .where(m.ConditionPassport.unit_id == unit_id)
        .order_by(m.ConditionPassport.graded_at.desc())
    ).scalars().first()
    if passport is not None and isinstance(passport.passport, dict):
        try:
            return float(passport.passport.get("grade_numeric") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


# (listing dto, listing row, unit, product, viewer wish match, best matched wish)
ScoredRow = tuple[RescueListing, m.RescueListing, m.ProductUnit | None, m.Product | None, float, m.ReverseWishlist | None]


def score_feed(
    db: Session,
    viewer: m.User | None,
    rows: list[ScoredRow],
    *,
    radius_km: float,
    engine,
) -> dict[str, DispatchScore]:
    """Score every visible rescue listing for `viewer` and return {id -> score}."""
    if not rows:
        return {}
    now = datetime.now(timezone.utc)

    # One pass over open wishes → demand near each unit (no per-listing query).
    wishes_by_cat: dict[str, list[m.ReverseWishlist]] = {}
    for w in open_wishes(db):
        wishes_by_cat.setdefault(w.category, []).append(w)
    radius = settings.rescue_default_radius_km

    candidates: list[DispatchCandidate] = []
    for (listing, row, unit, product, vmatch, best_w) in rows:
        category = product.category if product is not None else None
        national = (row.scope or "local") == "national"
        list_price = listing.list_price
        price_fit = size_fit = False
        size_fit = True
        if best_w is not None:
            if best_w.max_price is not None and list_price is not None:
                mx = float(best_w.max_price)
                price_fit = list_price <= mx <= list_price * (1 + settings.price_fit_band)
            if best_w.size:
                size_fit = bool(unit and unit.size) and (
                    unit.size.strip().lower() == str(best_w.size).strip().lower()
                )

        demand = _demand_for(unit, category, wishes_by_cat, radius) if unit is not None else None
        if national:
            channel, delivery_km = "refurb", settings.dispatch_national_delivery_km
        else:
            channel = "rescue"
            delivery_km = (
                listing.distance_km
                if listing.distance_km is not None
                else (demand.nearest_km if demand and demand.nearest_km else 5.0)
            )

        candidates.append(DispatchCandidate(
            listing_id=listing.id,
            unit_id=listing.unit_id,
            channel=channel,
            scope=row.scope or "local",
            grade_numeric=_grade_numeric(db, listing.unit_id),
            distance_km=listing.distance_km,
            radius_km=radius_km,
            delivery_km=float(delivery_km),
            ttl_remaining_frac=_ttl_remaining_frac(row, now),
            transfer_count=int(unit.transfer_count) if unit is not None else 0,
            demand=demand,
            viewer_wish_match=vmatch,
            price_fit=price_fit,
            size_fit=size_fit,
            discount_pct=listing.current_discount_pct or listing.discount_pct,
        ))

    req = DispatchRequest(
        viewer=DispatchViewer(
            user_id=str(viewer.id) if viewer is not None else None,
            eligible=bool(viewer.rescue_eligible) if viewer is not None else True,
            return_rate=float(viewer.return_rate) if viewer is not None and viewer.return_rate is not None else 0.0,
        ),
        candidates=candidates,
    )
    resp = engine.score_dispatch(req)
    return {s.listing_id: s for s in resp.scores}
