"""Pair Rescue (engine-pair-rescue): bipartite A↔B swap matching.

Find user pairs where A's returned unit satisfies B's open wish AND B's returned
unit satisfies A's wish, both within geo proximity. Pure circular economy — one
local leg each, no warehouse, no resale intermediary (lowest net CO₂).
"""

from __future__ import annotations

import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.geo import haversine_km
from app.models import entities as m
from app.schemas.dispatch import DispatchReason
from app.schemas.rescue import PairMatch

SIM_THRESHOLD = 0.6
OWNED_STATUSES = ("returned", "graded")


def _cosine(a, b) -> float:
    if a is None or b is None:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _best_unit_for_wish(units: list[m.ProductUnit], wish: m.ReverseWishlist) -> tuple[m.ProductUnit | None, float]:
    best, best_sim = None, 0.0
    for u in units:
        sim = _cosine(u.embedding, wish.embedding)
        if sim > best_sim:
            best, best_sim = u, sim
    return best, best_sim


def find_pairs(db: Session, radius_km: float | None = None, persist: bool = True) -> list[PairMatch]:
    radius = radius_km or settings.rescue_default_radius_km * 5

    units = db.execute(
        select(m.ProductUnit).where(m.ProductUnit.status.in_(OWNED_STATUSES))
        .where(m.ProductUnit.owner_id.isnot(None))
    ).scalars().all()
    wishes = db.execute(select(m.ReverseWishlist)).scalars().all()

    units_by_owner: dict = {}
    for u in units:
        units_by_owner.setdefault(u.owner_id, []).append(u)
    wishes_by_user: dict = {}
    for w in wishes:
        wishes_by_user.setdefault(w.user_id, []).append(w)

    owners = [o for o in units_by_owner if o in wishes_by_user]
    out: list[PairMatch] = []
    seen: set = set()

    for i, a in enumerate(owners):
        for b in owners[i + 1:]:
            # Best: A's unit ↔ B's wish, and B's unit ↔ A's wish.
            best_ab = max((_best_unit_for_wish(units_by_owner[a], wb) for wb in wishes_by_user[b]),
                          key=lambda t: t[1], default=(None, 0.0))
            best_ba = max((_best_unit_for_wish(units_by_owner[b], wa) for wa in wishes_by_user[a]),
                          key=lambda t: t[1], default=(None, 0.0))
            unit_a, sim_ab = best_ab
            unit_b, sim_ba = best_ba
            if unit_a is None or unit_b is None or sim_ab < SIM_THRESHOLD or sim_ba < SIM_THRESHOLD:
                continue

            distance = None
            if unit_a.geo_lat is not None and unit_b.geo_lat is not None:
                distance = haversine_km(unit_a.geo_lat, unit_a.geo_lng, unit_b.geo_lat, unit_b.geo_lng)
                if distance > radius:
                    continue

            key = tuple(sorted((str(unit_a.id), str(unit_b.id))))
            if key in seen:
                continue
            seen.add(key)

            score = round((sim_ab + sim_ba) / 2, 4)
            if persist and not db.execute(
                select(m.PairRescueMatch.id)
                .where(m.PairRescueMatch.unit_a == unit_a.id)
                .where(m.PairRescueMatch.unit_b == unit_b.id)
            ).first():
                db.add(m.PairRescueMatch(
                    unit_a=unit_a.id, unit_b=unit_b.id, user_a=a, user_b=b,
                    distance_km=distance, status="proposed",
                ))
            # A pair swap is the strongest dispatch edge: both wishes satisfied,
            # no payment, one local leg each → lowest net carbon (§21.4). Score it
            # on the same utility scale (mutual match, lifted for the zero-payment
            # circular win) and label why.
            dispatch_score = round(min(1.0, 0.5 * score + 0.5), 4)
            reasons = [
                DispatchReason(code="zero_payment_swap", label="Zero-payment swap"),
                DispatchReason(code="high_carbon_save", label="Lowest carbon — local swap"),
            ]
            if distance is not None and distance <= settings.rescue_default_radius_km * 2:
                reasons.append(DispatchReason(code="best_local_fit", label="Both nearby"))
            out.append(PairMatch(
                unit_a=str(unit_a.id), unit_b=str(unit_b.id), user_a=str(a), user_b=str(b),
                score=score, distance_km=round(distance, 2) if distance is not None else None,
                dispatch_score=dispatch_score, dispatch_reasons=reasons[:3],
            ))

    if persist:
        db.commit()
    # Best dispatch utility first (mutual-match swaps), score as the tiebreak.
    out.sort(key=lambda p: (p.dispatch_score or 0.0, p.score), reverse=True)
    return out
