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


# Pillar 5 — tiered early access. Each tier (by lifetime credits) gets a "lead":
# how long before a listing goes public it can already be seen. A listing goes
# public after the longest lead elapses, so the top tier sees it from creation.
def access_tiers() -> list[tuple[str, float, int]]:
    """(name, lifetime-credit threshold, lead seconds) ascending by threshold."""
    return [
        ("standard", 0.0, 0),
        ("silver", settings.rescue_early_access_credit_threshold,
         settings.rescue_early_access_window_seconds),
        ("gold", settings.rescue_early_access_gold_threshold,
         settings.rescue_early_access_gold_window_seconds),
    ]


def _public_window_seconds() -> int:
    return max(lead for _, _, lead in access_tiers())


def user_lead_seconds(credits_total: float) -> int:
    """The early-access lead the user has earned (0 = no early access)."""
    lead = 0
    for _, threshold, secs in access_tiers():
        if credits_total >= threshold:
            lead = max(lead, secs)
    return lead


def user_tier(credits_total: float) -> str:
    name = "standard"
    for tier_name, threshold, _ in access_tiers():
        if credits_total >= threshold:
            name = tier_name
    return name


def has_early_access(credits_total: float) -> bool:
    return user_lead_seconds(credits_total) > 0


def _created(listing: m.RescueListing) -> datetime:
    created = listing.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created


def early_access_until(listing: m.RescueListing) -> datetime:
    """When the listing goes fully public: created_at + longest tier lead."""
    return _created(listing) + timedelta(seconds=_public_window_seconds())


def is_embargoed(listing: m.RescueListing, now: datetime | None = None) -> bool:
    """True while the listing is still pre-public (some tier embargo remains).

    National (Path B) certified relists are never embargoed — they are the
    nationwide fallback, not a hyperlocal early-access drop.
    """
    if getattr(listing, "scope", "local") == "national":
        return False
    now = now or datetime.now(timezone.utc)
    return now < early_access_until(listing)


def visible_to(listing: m.RescueListing, lead_seconds: int, now: datetime | None = None) -> bool:
    """Can a user with this lead see the listing yet?"""
    if getattr(listing, "scope", "local") == "national":
        return True
    now = now or datetime.now(timezone.utc)
    return now >= early_access_until(listing) - timedelta(seconds=lead_seconds)


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


def has_active_listing(db, unit_id) -> bool:
    return db.execute(
        select(m.RescueListing.id)
        .where(m.RescueListing.unit_id == unit_id)
        .where(m.RescueListing.status == "active")
        .limit(1)
    ).first() is not None


def create_listing_for_disposition(
    db,
    *,
    unit: m.ProductUnit,
    channel: str,
    anchored_at: datetime | None = None,
    has_local_demand: bool = True,
    discount_pct: float | None = None,
) -> m.RescueListing | None:
    """Create the right listing for a disposition outcome (idempotent per unit).

    Path A (local, hyperlocal intercept): good grade + local demand → a
    pickup-anchored, time-decayed rescue listing. The decay clock starts at
    pickup (`anchored_at`), not at return time.

    Path B (national, Certified Second-Life): refurb channel, or rescue with no
    local taker → a flat-discount national relist (shipped, no time decay). Adds
    REFURBISHED + RELISTED ledger events.

    `discount_pct` overrides the local base discount (e.g. a pristine size return
    or in-window exchange lists at only a minimal markdown). Forces Path A so the
    near-original-price unit is offered locally before pickup.
    """
    if has_active_listing(db, unit.id):
        return None

    now = datetime.now(timezone.utc)
    anchored_at = anchored_at or now

    go_national = channel in ("refurb", "refurbish") or not has_local_demand
    if discount_pct is not None:
        go_national = False  # minimal-discount (pristine) units stay local Path A
    if go_national and not settings.rescue_national_enabled:
        go_national = False

    if go_national:
        listing = m.RescueListing(
            unit_id=unit.id,
            base_discount_pct=settings.rescue_national_discount_pct,
            current_discount_pct=settings.rescue_national_discount_pct,
            ttl_seconds=None, expires_at=None,  # no time decay for national relist
            status="active", scope="national", fulfillment="shipped",
            created_at=now,
        )
        db.add(listing)
        db.add(m.LifeLedgerEvent(unit_id=unit.id, event_type="REFURBISHED"))
        db.add(m.LifeLedgerEvent(unit_id=unit.id, event_type="RELISTED"))
    else:
        base = discount_pct if discount_pct is not None else settings.rescue_discount_base
        ttl = int(settings.rescue_local_ttl_hours * 3600)
        listing = m.RescueListing(
            unit_id=unit.id,
            base_discount_pct=base,
            current_discount_pct=base,
            ttl_seconds=ttl,
            expires_at=anchored_at + timedelta(seconds=ttl),
            status="active", scope="local", fulfillment="local_pickup",
            created_at=anchored_at,  # pickup-anchored decay clock
        )
        db.add(listing)
    return listing
