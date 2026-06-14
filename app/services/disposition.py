"""Disposition orchestration (api-disposition).

Builds the demand signal (reverse-wishlist demand near the unit) that feeds the
engine's demand-weighted routing, then records the outcome: impact event +
green credits + a LifeLedger event for the chosen channel.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.ledger_client import get_ledger_client
from app.core.carbon import credits_for_co2
from app.core.config import settings
from app.core.geo import geo_decay, haversine_km
from app.models import entities as m
from app.schemas.disposition import DemandSignal, DispositionResponse

CREDIT_UNLOCK_DAYS = 14

# Channel -> LifeLedger event_type (only channels that produce a ledger event).
_CHANNEL_EVENT = {
    "rescue": "RESCUED",
    "p2p_resale": "P2P_LISTED",
    "exchange": "EXCHANGED",
    "donate": "DONATED",
    "recycle": "RECYCLED",
}


def build_demand_signal(db: Session, *, category: str, unit: m.ProductUnit) -> DemandSignal:
    radius = settings.rescue_default_radius_km
    now = datetime.now(timezone.utc)
    wishes = db.execute(
        select(m.ReverseWishlist).where(m.ReverseWishlist.category == category)
    ).scalars().all()

    count = 0
    score = 0.0
    nearest: float | None = None
    for w in wishes:
        if w.expires_at and w.expires_at < now:
            continue
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
        open_wish_count=count,
        demand_score=round(score, 3),
        nearest_km=round(nearest, 3) if nearest is not None else None,
    )


def record_outcome(
    db: Session,
    *,
    user_id,
    unit: m.ProductUnit,
    decision: DispositionResponse,
    passport_hash: str | None = None,
) -> None:
    co2 = decision.net_co2_saved_kg or 0.0
    db.add(m.ImpactEvent(user_id=user_id, unit_id=unit.id, channel=decision.channel, co2_saved_kg=co2))
    credits = credits_for_co2(co2)
    # Locality bonus: a hyperlocal rescue keeps the carbon win local, so it earns
    # richer credits than a shipped national disposition.
    if decision.channel == "rescue":
        credits = round(credits * settings.credit_locality_multiplier, 2)
    if credits > 0:
        # Keep-based credits unlock after a 14-day hold (anti-abuse).
        db.add(m.GreenCreditLedger(
            user_id=user_id, amount=credits, reason=f"disposition:{decision.channel}",
            unlock_at=datetime.now(timezone.utc) + timedelta(days=CREDIT_UNLOCK_DAYS),
        ))
    event_type = _CHANNEL_EVENT.get(decision.channel)
    if event_type:
        tx_hash = None
        if passport_hash:
            tx_hash = get_ledger_client().anchor(
                unit_id=str(unit.id), passport_hash=passport_hash
            ).tx_hash
        db.add(m.LifeLedgerEvent(
            unit_id=unit.id, event_type=event_type, passport_hash=passport_hash, tx_hash=tx_hash,
        ))
    db.commit()
