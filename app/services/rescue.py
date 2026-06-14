"""Rescue pricing + guardrails (engine-rescue-decay, api-rescue).

Decay pricing: discount rises as TTL drops — the countdown becomes a price clock.
Recomputed on each feed read (plan.md §7).
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import settings
from app.models import entities as m


def current_discount(listing: m.RescueListing, now: datetime | None = None) -> float:
    """base + (max - base) × (1 - ttl_remaining / ttl_total), floored at base."""
    base = listing.base_discount_pct or settings.rescue_discount_base
    ceiling = settings.rescue_discount_max
    if not listing.ttl_seconds or not listing.expires_at:
        return round(base, 4)

    now = now or datetime.now(timezone.utc)
    expires = listing.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    remaining = (expires - now).total_seconds()
    remaining = max(0.0, min(remaining, listing.ttl_seconds))
    elapsed_frac = 1.0 - (remaining / listing.ttl_seconds)
    return round(base + (ceiling - base) * elapsed_frac, 4)


def claim_guardrails(db, listing: m.RescueListing, user: m.User | None) -> list[str]:
    blocks: list[str] = []
    if listing.status != "active":
        blocks.append(f"listing_{listing.status}")
    if user is None:
        blocks.append("unknown_user")
        return blocks
    if not user.rescue_eligible:
        blocks.append("user_not_rescue_eligible")
    if user.return_rate >= settings.rescue_user_return_rate_cap:
        blocks.append(f"return_rate>={settings.rescue_user_return_rate_cap}")

    unit = db.get(m.ProductUnit, listing.unit_id)
    if unit is not None and unit.transfer_count >= settings.chain_depth_cap:
        blocks.append(f"chain_depth_cap({unit.transfer_count}>={settings.chain_depth_cap})")
    return blocks
