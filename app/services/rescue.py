"""Rescue pricing + guardrails (engine-rescue-decay, api-rescue).

Decay pricing: discount rises as TTL drops — the countdown becomes a price clock.
Recomputed on each feed read (plan.md §7).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

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


def lifetime_credits(db, user_id) -> float:
    """Total green credits the user has *ever* earned (locked + unlocked).

    Participation tier — not the spendable balance. The more you rescue, the
    higher your tier, the better your access (Pillar 5 flywheel).
    """
    total = db.execute(
        select(func.coalesce(func.sum(m.GreenCreditLedger.amount), 0)).where(
            m.GreenCreditLedger.user_id == user_id
        )
    ).scalar_one()
    return float(total or 0)


def has_early_access(credits_total: float) -> bool:
    return credits_total >= settings.rescue_early_access_credit_threshold


def early_access_until(listing: m.RescueListing) -> datetime:
    """End of the embargo window: created_at + early-access window."""
    created = listing.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created + timedelta(seconds=settings.rescue_early_access_window_seconds)


def is_embargoed(listing: m.RescueListing, now: datetime | None = None) -> bool:
    """True while the listing is still inside its early-access window."""
    now = now or datetime.now(timezone.utc)
    return now < early_access_until(listing)


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
