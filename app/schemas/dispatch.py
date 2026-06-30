"""Rescue Dispatch Score schemas (plan.md §21.4).

The HTTP contract for relay-engine's `POST /dispatch/score`: relay-api precomputes
each (rescue listing × viewer) edge's signals from Postgres and the engine returns
a weighted utility `dispatch_score` + explainable `dispatch_reasons` per listing.
Mirrors `relay-engine/internal/models/models.go`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.disposition import DemandSignal


class DispatchReason(BaseModel):
    """A human, explainable reason a listing is surfaced ("Matches your wish")."""

    code: str
    label: str


class DispatchViewer(BaseModel):
    """The buyer the feed is being scored for (powers the risk terms)."""

    user_id: str | None = None
    eligible: bool = True
    return_rate: float = 0.0


class DispatchCandidate(BaseModel):
    """One rescue listing edge (this unit × this viewer) — all signals precomputed."""

    listing_id: str
    unit_id: str | None = None
    channel: str = "rescue"  # rescue | refurb (carbon base)
    scope: str = "local"  # local | national

    grade_numeric: float = 0.0
    # Viewer→unit distance (km); None ⇒ ships (national, no local distance win).
    distance_km: float | None = None
    radius_km: float = 15.0
    # Last-mile estimate for the carbon term.
    delivery_km: float = 5.0
    # remaining/ttl in [0,1] (1=fresh, 0=expiring); None ⇒ no decay (national).
    ttl_remaining_frac: float | None = None
    transfer_count: int = 0
    # Open-wish demand near the unit (the signal that also feeds disposition).
    demand: DemandSignal | None = None
    # 0..1 — how strongly THIS viewer's own open wishes want this unit.
    viewer_wish_match: float = 0.0
    price_fit: bool = False
    size_fit: bool = True
    discount_pct: float = 0.0


class DispatchRequest(BaseModel):
    viewer: DispatchViewer
    candidates: list[DispatchCandidate] = Field(default_factory=list)


class DispatchScore(BaseModel):
    listing_id: str
    dispatch_score: float
    dispatch_reasons: list[DispatchReason] = Field(default_factory=list)


class DispatchResponse(BaseModel):
    scores: list[DispatchScore] = Field(default_factory=list)
