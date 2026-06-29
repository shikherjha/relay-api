"""Shared SKU return-health signals (single source for Ops + Return Confidence).

Aggregates `return_events` per SKU into a count, a dominant reason, an approximate
return rate, and a flagged/recommendation verdict. The seller Ops dashboard
(`routers/ops.py`) and the customer-facing Return Confidence layer
(`services/return_confidence.py`) both read from here, so prevention and the
seller dashboard tell ONE story (plan.md §21.1 acceptance).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import entities as m

# Reason -> proactive seller catalog fix (seller-side return signal).
REASON_FIX = {
    "not_as_described": "review listing copy + product photos",
    "too_small": "add a size-up note / update size chart",
    "too_large": "add a size-down note / update size chart",
    "fit": "update size chart + fit guidance",
    "defective": "audit supplier QC for this SKU",
    # Fulfillment-accuracy signal: wrong item shipped → pick-pack / SKU mapping.
    "wrong_item": "audit pick-pack accuracy + SKU-to-bin mapping",
}

# Only emit a catalog-fix recommendation once a SKU crosses these thresholds.
SELLER_SIGNAL_MIN_RETURNS = 2
SELLER_SIGNAL_MIN_RATE = 0.25


@dataclass(frozen=True)
class SkuHealth:
    sku: str
    title: str | None
    return_count: int
    return_rate: float
    dominant_reason: str | None
    # Share of this SKU's returns attributable to the dominant reason (0–1).
    # Powers the electronics "what people actually returned this for" preempt.
    dominant_share: float
    flagged: bool
    recommendation: str | None


def aggregate_sku_health(db: Session) -> list[SkuHealth]:
    """Per-SKU return health, most-returned first."""
    rows = db.execute(
        select(m.Product.sku, m.Product.title, m.ReturnEvent.reason_code)
        .join(m.ProductUnit, m.ProductUnit.id == m.ReturnEvent.unit_id)
        .join(m.Product, m.Product.id == m.ProductUnit.product_id)
    ).all()

    counts: dict[str, int] = defaultdict(int)
    titles: dict[str, str] = {}
    reasons: dict[str, Counter] = defaultdict(Counter)
    for sku, title, reason in rows:
        counts[sku] += 1
        titles[sku] = title
        reasons[sku][reason] += 1

    out: list[SkuHealth] = []
    for sku, n in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        top = reasons[sku].most_common(1)[0] if reasons[sku] else None
        dominant = top[0] if top else None
        share = (top[1] / n) if (top and n) else 0.0
        rate = min(n / 10.0, 1.0)
        flagged = n >= SELLER_SIGNAL_MIN_RETURNS or rate >= SELLER_SIGNAL_MIN_RATE
        out.append(SkuHealth(
            sku=sku, title=titles.get(sku), return_count=n, return_rate=rate,
            dominant_reason=dominant, dominant_share=round(share, 2), flagged=flagged,
            recommendation=REASON_FIX.get(dominant) if (flagged and dominant) else None,
        ))
    return out


def sku_health_map(db: Session) -> dict[str, SkuHealth]:
    """SKU -> health, for O(1) lookup while scoring a cart/PDP."""
    return {h.sku: h for h in aggregate_sku_health(db)}
