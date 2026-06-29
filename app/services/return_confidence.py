"""Return Confidence — non-punitive purchase-keep prediction (plan.md §21.1).

Upgrades prevention beyond the single bracketing rule into a small
user × product × cart scoring layer. It borrows the *framing* of Returnformer /
COP-HGNN (returns are a user-product problem, not one cart rule) and Amazon
Fashion Fit Insights (size-system + keep history + SKU return health), but the
MVP is a deterministic, explainable heuristic — no model dependency, so it is
always available for the live demo and degrades gracefully if relay-ml is down.

Score signals (each additive + clamped):
- Cart        : bracketing (≥3 sizes of one item) · duplicate variant (2 sizes).
- Product/SKU : shared return health (`services.return_signals`) — rate +
                dominant reason, so Ops and prevention tell ONE story.
- User fit    : stored `fit_profile` for the item's size axis (tops/bottoms/
                shoes). A known matching size RAISES confidence; an unknown axis
                or a size differing from the usual one lowers it.
- Fit signal  : optional review-derived fit flag (runs small/large) from relay-ml.
- User history: first purchase from a brand; overall return rate (SILENT — folded
                into the score but never surfaced to the shopper, per guardrail).

Guardrail: customer copy is confidence-building ("Size M is the safer pick"),
never "you are likely to return this". The risk wording lives on the Ops side.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import entities as m
from app.schemas.return_confidence import (
    ConfidenceDriver,
    ConfidenceIntervention,
    ProductConfidence,
    ReturnConfidence,
)
from app.services import fit_profiles as fp_service
from app.services.return_signals import SkuHealth, sku_health_map

# Which fit-profile axis covers a category (mirrors matching._FIT_AXIS).
_FIT_AXIS = {
    "jeans": "bottoms", "pants": "bottoms", "trousers": "bottoms",
    "shorts": "bottoms", "skirt": "bottoms",
    "sneakers": "shoes", "shoes": "shoes", "footwear": "shoes",
}
_SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL"]

_BASE_SCORE = 0.92
HIGH_BAND = 0.75
MEDIUM_BAND = 0.5

# Score deltas — kept here so the whole model is legible in one place.
_PENALTY_BRACKETING = 0.45
_PENALTY_DUPLICATE = 0.20
_PENALTY_SKU_HIGH = 0.26
_PENALTY_SKU_MED = 0.14
_PENALTY_SIZE_MISMATCH = 0.10
_PENALTY_SIZE_UNKNOWN = 0.06
_PENALTY_FIT_FLAG = 0.05
_PENALTY_NEW_BRAND = 0.05
_BONUS_SIZE_MATCH = 0.06

_SEV_RANK = {"high": 3, "medium": 2, "low": 1}

# Customer-facing, confidence-building copy for a SKU's dominant return reason.
_REASON_CUSTOMER_COPY = {
    "too_small": "Shoppers say this runs small — size up for the best fit.",
    "too_large": "Shoppers say this runs large — size down for the best fit.",
    "fit": "Fit varies on this item — check the size chart before you buy.",
    "not_as_described": "Check the photos and details so it's exactly what you expect.",
    "defective": "Every unit is condition-graded — buy with our guarantee.",
}

_FIT_FLAG_COPY = {
    "runs_small": "Tends to run small — size up if you're between sizes.",
    "runs_large": "Tends to run large — size down if you're between sizes.",
    "critical_fit": "Fit is tight on this item — check the measurements.",
}

# Electronics "fit-for-purpose" (§21.1 Phase 3): the analog of size is *will it
# work for me?* A short, deterministic compatibility checklist per category — the
# biggest "not as described" killer — surfaced as a confidence-builder on the PDP.
_COMPAT_KEY_ALIASES = {
    "earphones": "headphones", "earbuds": "headphones", "headset": "headphones",
    "macbook": "laptop", "notebook": "laptop",
    "iphone": "smartphone", "android": "smartphone", "phone": "smartphone",
    "watch": "smartwatch",
}
_COMPAT_CHECKLIST = {
    "headphones": ["Wireless or wired — and your device's port", "Noise cancelling: active vs passive", "Pairs with your phone / laptop"],
    "speaker": ["Bluetooth vs Wi-Fi", "Battery vs plug-in", "Right size for your room"],
    "laptop": ["OS: Windows or macOS", "RAM & storage you need", "Ports you rely on (USB-C / HDMI)"],
    "smartphone": ["Storage variant", "Supports your network bands", "Works with your SIM / region"],
    "tablet": ["Storage variant", "Wi-Fi vs cellular", "Stylus / keyboard support"],
    "monitor": ["Size & resolution", "Ports (HDMI / USB-C / DP)", "Refresh rate for your use"],
    "keyboard": ["Wireless or wired", "Layout (US / UK)", "Pairs with your device"],
    "mouse": ["Wireless or wired", "Works with your device", "DPI / ergonomics"],
    "camera": ["Lens mount compatibility", "Kit lens included?", "Works with your accessories"],
    "smartwatch": ["Phone OS (iOS / Android)", "Band size", "GPS vs cellular"],
}
_COMPAT_DEFAULT = ["Works with your devices", "Key specs match your needs", "What's in the box"]


def _compat_checklist(category: str | None) -> list[str]:
    key = (category or "").strip().lower()
    key = _COMPAT_KEY_ALIASES.get(key, key)
    if key in _COMPAT_CHECKLIST:
        return _COMPAT_CHECKLIST[key]
    # Substring match for compound categories ("gaming laptop", "over-ear headphones").
    for alias, canon in _COMPAT_KEY_ALIASES.items():
        if alias in key:
            return _COMPAT_CHECKLIST.get(canon, _COMPAT_DEFAULT)
    for canon in _COMPAT_CHECKLIST:
        if canon in key:
            return _COMPAT_CHECKLIST[canon]
    return _COMPAT_DEFAULT


# Electronics differentiator #1 — "works with what you own". Map a product's
# category to the device class it pairs with, so we can render a *verdict*
# ("Pairs with your iPhone ✓") from the buyer's history/setup rather than a
# generic checklist that just duplicates the PDP description/FAQ.
_DEVICE_GROUP = {  # category alias -> owned-device class it depends on
    "headphones": "phone", "earphones": "phone", "earbuds": "phone",
    "headset": "phone", "speaker": "phone", "smartwatch": "phone", "watch": "phone",
    "mouse": "laptop", "keyboard": "laptop", "monitor": "laptop",
}
_OWN_GROUP = {  # how a kept purchase's category registers as an owned device
    "smartphone": "phone", "iphone": "phone", "android": "phone", "phone": "phone",
    "laptop": "laptop", "macbook": "laptop", "notebook": "laptop",
    "tablet": "tablet",
}


def _own_group(category: str | None) -> str | None:
    key = (category or "").strip().lower()
    if key in _OWN_GROUP:
        return _OWN_GROUP[key]
    for alias, group in _OWN_GROUP.items():
        if alias in key:
            return group
    return None


def _owned_devices(db: Session, user_id: str) -> dict[str, str]:
    """Devices the buyer already OWNS, from kept electronics orders → {group: label}.
    Drives the personalized compatibility verdict ("Pairs with your <device>")."""
    rows = db.execute(
        select(m.Product.category, m.Product.title)
        .join(m.OrderItem, m.OrderItem.product_id == m.Product.id)
        .join(m.Order, m.Order.id == m.OrderItem.order_id)
        .outerjoin(m.ReturnEvent, m.ReturnEvent.order_item_id == m.OrderItem.id)
        .where(m.Order.user_id == user_id)
        .where(m.Product.vertical == "electronics")
        .where(m.OrderItem.return_state.is_(None))
        .where(m.ReturnEvent.id.is_(None))
        .order_by(m.OrderItem.created_at.desc())
    ).all()
    owned: dict[str, str] = {}
    for category, title in rows:
        group = _own_group(category)
        if group and group not in owned and title:
            owned[group] = title
    return owned


def _compat_target(category: str | None) -> str | None:
    key = (category or "").strip().lower()
    key = _COMPAT_KEY_ALIASES.get(key, key)
    if key in _DEVICE_GROUP:
        return _DEVICE_GROUP[key]
    for alias, group in _DEVICE_GROUP.items():
        if alias in key:
            return group
    return None


def _compat_verdict(product: m.Product, ctx: "ProfileCtx") -> str | None:
    """A personalized "works with what you own" verdict, or None to fall back to
    the generic checklist. SELF only (we don't know a giftee's devices)."""
    if not ctx.for_self:
        return None
    target = _compat_target(product.category)
    if not target:
        return None
    owned_label = ctx.owned.get(target)
    if owned_label:
        return f"Pairs with your {owned_label}"
    setup_val = ctx.setup.get(target)
    if setup_val:
        if target == "phone":
            os_label = "iPhone" if setup_val.lower() in ("ios", "iphone", "apple") else f"{setup_val.title()} phone"
            return f"Set up for your {os_label}"
        if target == "laptop":
            return f"Matches your {setup_val.upper()} laptop"
        return f"Works with your {setup_val}"
    return None


_HEADLINES = {
    "high": "You're set — buy one with confidence.",
    "medium": "Almost there — one quick tweak helps you keep it.",
    "low": "Let's get you the one that fits.",
}


@dataclass
class CartLine:
    """One intended purchase line (a cart row, or a single PDP selection)."""

    product: m.Product
    size: str | None = None
    variant: str | None = None
    line_id: str | None = None
    # Recipient (Fit Profile id) this line is for; None/"anyone" = unassigned gift.
    profile_id: str | None = None


@dataclass
class ProfileCtx:
    """The resolved "shopping for" profile that personalizes the scoring.

    `fit_map` is the profile's axis→size view; `for_self` gates personal-to-the-
    buyer signals (own brand history / return rate). `name`/`possessive`/`object`
    drive the size-copy ("Matches your usual M" vs "Matches Priya's usual M").
    `inferred_axes` are sizes filled from the buyer's own order history (Phase 2),
    so the copy reads "Based on your recent orders" instead of "your usual size".
    """

    id: str = fp_service.SELF_ID
    name: str = "You"
    for_self: bool = True
    fit_map: dict = None  # type: ignore[assignment]
    inferred_axes: set = None  # type: ignore[assignment]
    # "Anyone" — an unassigned gift line: don't personalize size at all (no
    # opinion, so no penalty), just product-level signals + reassurance.
    anonymous: bool = False
    # Electronics personalization (self only): saved device setup + devices the
    # buyer already owns (from order history) → the compatibility verdict.
    setup: dict = None  # type: ignore[assignment]
    owned: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.fit_map is None:
            self.fit_map = {}
        if self.inferred_axes is None:
            self.inferred_axes = set()
        if self.setup is None:
            self.setup = {}
        if self.owned is None:
            self.owned = {}

    @property
    def possessive(self) -> str:
        return "your" if self.for_self else f"{self.name}'s"

    @property
    def object_pronoun(self) -> str:
        return "you" if self.for_self else self.name


def _band(score: float) -> str:
    if score >= HIGH_BAND:
        return "high"
    if score >= MEDIUM_BAND:
        return "medium"
    return "low"


def _axis_for(category: str | None) -> str:
    return _FIT_AXIS.get((category or "").lower(), "tops")


def _suggest_size(sizes: list[str], fit_profile: dict, axis: str) -> str | None:
    """Prefer the buyer's known size for this axis; else the median bracketed size."""
    if fit_profile.get(axis) in sizes:
        return fit_profile[axis]
    ordered = [s for s in _SIZE_ORDER if s in sizes] or sorted(sizes)
    return ordered[len(ordered) // 2] if ordered else None


def _brand_of(product: m.Product) -> str | None:
    md = product.product_metadata
    return md.get("brand") if isinstance(md, dict) else None


def _return_rate_penalty(rate: float) -> float:
    """Silent: folded into the score but never shown to the shopper (guardrail)."""
    if rate >= 0.4:
        return 0.15
    if rate >= 0.25:
        return 0.08
    return 0.0


def _sku_health_label(h: SkuHealth) -> str:
    reason = (h.dominant_reason or "fit").replace("_", " ")
    return f"Elevated {reason} returns on this item"


def _purchased_brands(db: Session, user_id: str) -> set[str]:
    rows = db.execute(
        select(m.Product.product_metadata)
        .join(m.OrderItem, m.OrderItem.product_id == m.Product.id)
        .join(m.Order, m.Order.id == m.OrderItem.order_id)
        .where(m.Order.user_id == user_id)
    ).all()
    brands: set[str] = set()
    for (md,) in rows:
        if isinstance(md, dict) and md.get("brand"):
            brands.add(md["brand"])
    return brands


def _history_fit_map(db: Session, user_id: str) -> dict[str, str]:
    """Phase 2: infer the buyer's likely size per axis from their own KEPT orders.

    "Kept" = a delivered line with no post-return state AND no return event. We
    take the most recent kept size per axis, so a shopper who never set a profile
    still gets a sharp recommendation grounded in what they actually keep. SELF
    only — we don't have another person's order history (attribution is future
    work), so this never runs for someone they're shopping for.
    """
    rows = db.execute(
        select(m.Product.category, m.OrderItem.size)
        .join(m.Order, m.Order.id == m.OrderItem.order_id)
        .join(m.Product, m.Product.id == m.OrderItem.product_id)
        .outerjoin(m.ReturnEvent, m.ReturnEvent.order_item_id == m.OrderItem.id)
        .where(m.Order.user_id == user_id)
        .where(m.Product.vertical == "fashion")
        .where(m.OrderItem.size.isnot(None))
        .where(m.OrderItem.return_state.is_(None))
        .where(m.ReturnEvent.id.is_(None))
        .order_by(m.OrderItem.created_at.desc())
    ).all()
    out: dict[str, str] = {}
    for category, size in rows:
        axis = _axis_for(category)
        if axis not in out and size:
            out[axis] = size
    return out


def _score_product(
    group: list[CartLine],
    ctx: ProfileCtx,
    sku_health: dict[str, SkuHealth],
    fit_flags: dict[str, list[str]],
    purchased_brands: set[str],
) -> ProductConfidence:
    product = group[0].product
    pid = str(product.id)
    is_fashion = product.vertical == "fashion"
    axis = _axis_for(product.category)
    brand = _brand_of(product)
    sizes = sorted({(ln.size or ln.variant) for ln in group if (ln.size or ln.variant)})
    distinct = len(sizes)
    selected_size = group[0].size
    line_ids = [ln.line_id for ln in group if ln.line_id]
    in_bag = "in the bag" if not ctx.for_self else "in your bag"

    score = _BASE_SCORE
    drivers: list[ConfidenceDriver] = []
    interventions: list[ConfidenceIntervention] = []
    # The single best size for this profile (drives the Amazon-native PDP line).
    rec_size: str | None = None
    rec_reason: str | None = None
    # Electronics "what people returned this for" preempt.
    return_reason: str | None = None
    return_reason_share: float | None = None

    # 1) Bracketing / duplicate variant (cart-shape signal).
    if distinct >= 3:
        score -= _PENALTY_BRACKETING
        suggested = _suggest_size(sizes, ctx.fit_map, axis)
        rec_size = suggested
        drivers.append(ConfidenceDriver(
            type="bracketing", severity="high",
            label=f"{distinct} sizes of this item {in_bag}",
        ))
        interventions.append(ConfidenceIntervention(
            type="size_recommendation", action="remove_extra_sizes",
            product_id=pid, suggested_size=suggested,
            label=(f"Keep size {suggested} — buy one with confidence"
                   if suggested else "Keep one size — buy with confidence"),
        ))
    elif distinct == 2:
        score -= _PENALTY_DUPLICATE
        suggested = _suggest_size(sizes, ctx.fit_map, axis)
        rec_size = suggested
        drivers.append(ConfidenceDriver(
            type="duplicate_variant", severity="medium",
            label=f"2 sizes of this item {in_bag}",
        ))
        interventions.append(ConfidenceIntervention(
            type="size_recommendation", action="remove_extra_sizes",
            product_id=pid, suggested_size=suggested,
            label=(f"Pick size {suggested} and drop the spare"
                   if suggested else "Pick one size and drop the spare"),
        ))

    # 2) SKU return health (shared with Ops). wrong_item is a fulfilment issue,
    # not a buyer-fit signal, so it never reaches the shopper.
    h = sku_health.get(product.sku) if product.sku else None
    if h and h.flagged and h.dominant_reason and h.dominant_reason != "wrong_item":
        sev = "high" if (h.return_rate >= 0.4 or h.return_count >= 4) else "medium"
        score -= _PENALTY_SKU_HIGH if sev == "high" else _PENALTY_SKU_MED
        drivers.append(ConfidenceDriver(
            type="sku_return_health", severity=sev, label=_sku_health_label(h),
        ))
        copy = _REASON_CUSTOMER_COPY.get(h.dominant_reason)
        if is_fashion:
            if copy:
                interventions.append(ConfidenceIntervention(
                    type="fit_review", action="review_fit", product_id=pid, label=copy,
                ))
        else:
            # Electronics differentiator #2 — surface the SKU's REAL dominant return
            # reason + share (data Amazon's static FAQ never shows) and preempt it.
            return_reason = h.dominant_reason
            return_reason_share = h.dominant_share
            interventions.append(ConfidenceIntervention(
                type="return_insight", product_id=pid,
                label=copy or "Check the specs and photos so it's exactly what you expect.",
            ))

    # 3) Profile fit confidence (fashion only) — uses the selected person's anchor.
    #    Skipped for an "Anyone" gift line: we have no size opinion, so no penalty.
    if is_fashion and not ctx.anonymous:
        known = ctx.fit_map.get(axis)
        # When the size came from order-history inference (Phase 2), the copy reads
        # "your recent" instead of "your usual" so it's honest about its basis.
        inferred = axis in ctx.inferred_axes
        basis = "recent" if inferred else "usual"
        if not known:
            score -= _PENALTY_SIZE_UNKNOWN
            drivers.append(ConfidenceDriver(
                type="size_uncertainty", severity="low",
                label=f"No saved size for {ctx.possessive} {axis}",
            ))
            interventions.append(ConfidenceIntervention(
                type="fit_profile", action="add_fit_profile", product_id=pid,
                label=(f"Save {ctx.possessive} size for sharper fit picks"),
            ))
        else:
            rec_size = rec_size or known
            if distinct <= 1:
                # Only meaningful when there's a single intended size.
                size_in_q = selected_size or (sizes[0] if sizes else None)
                if size_in_q and size_in_q == known:
                    score += _BONUS_SIZE_MATCH
                    rec_reason = (
                        "from your recent orders" if inferred
                        else f"matches {ctx.possessive} usual fit"
                    )
                    drivers.append(ConfidenceDriver(
                        type="fit_confidence", severity="low", positive=True,
                        label=f"Matches {ctx.possessive} {basis} {axis} size ({known})",
                    ))
                elif size_in_q and size_in_q != known:
                    score -= _PENALTY_SIZE_MISMATCH
                    rec_reason = f"{ctx.possessive} {basis} size"
                    drivers.append(ConfidenceDriver(
                        type="size_mismatch", severity="medium",
                        label=f"Different from {ctx.possessive} {basis} size ({known})",
                    ))
                    interventions.append(ConfidenceIntervention(
                        type="size_recommendation", product_id=pid, suggested_size=known,
                        label=f"Size {known} is the safer pick for {ctx.object_pronoun}",
                    ))

        # 3b) Review-derived fit flag (best-effort enrichment).
        for flag in fit_flags.get(pid, []):
            if flag in _FIT_FLAG_COPY:
                score -= _PENALTY_FIT_FLAG
                drivers.append(ConfidenceDriver(
                    type="fit_signal", severity="low", label=_FIT_FLAG_COPY[flag],
                ))
                break
    elif not is_fashion:
        # Electronics "fit-for-purpose" (§21.1 Phase 3): the analog of size is
        # *will this work for me?*. No size penalty. We beat the static PDP
        # description/FAQ two ways: a personalized verdict from what the buyer
        # already OWNS/their setup, and the return-reason preempt (done in §2).
        verdict = _compat_verdict(product, ctx)
        if verdict:
            drivers.append(ConfidenceDriver(
                type="compatibility_match", severity="low", positive=True, label=verdict,
            ))
        # Always carry the checklist so the PDP guidance box has substance.
        interventions.append(ConfidenceIntervention(
            type="compatibility_check", action="review_compatibility", product_id=pid,
            label="Will this work for you?", items=_compat_checklist(product.category),
        ))
        # Offer the 1-tap setup capture when we couldn't personalize it ourselves.
        if ctx.for_self and not verdict and _compat_target(product.category):
            interventions.append(ConfidenceIntervention(
                type="setup_capture", action="add_setup", product_id=pid,
                label="Tell us your setup for an instant compatibility check",
            ))

    # 4) First-time brand — a personal-history signal, so SELF only. We don't
    #    track another person's purchase history, so it never applies to them.
    if is_fashion and ctx.for_self and brand and brand not in purchased_brands:
        score -= _PENALTY_NEW_BRAND
        drivers.append(ConfidenceDriver(
            type="new_brand", severity="low", label=f"First time buying {brand}",
        ))

    score = max(0.05, min(0.99, score))
    return ProductConfidence(
        product_id=pid, title=product.title, size=selected_size,
        line_ids=line_ids,
        profile_id=ctx.id, profile_name=ctx.name, for_self=ctx.for_self,
        keep_score=round(score, 3), confidence_band=_band(score),
        recommended_size=rec_size, recommended_reason=rec_reason,
        return_reason=return_reason, return_reason_share=return_reason_share,
        drivers=drivers, interventions=interventions,
    )


def _merge_drivers(groups: Iterable[list[ConfidenceDriver]]) -> list[ConfidenceDriver]:
    seen: dict[tuple, ConfidenceDriver] = {}
    for drivers in groups:
        for d in drivers:
            seen.setdefault((d.type, d.label), d)
    out = list(seen.values())
    # Risk drivers first (by severity), positive reassurance last.
    out.sort(key=lambda d: (d.positive, -_SEV_RANK.get(d.severity, 0)))
    return out


def _merge_interventions(
    groups: Iterable[list[ConfidenceIntervention]],
) -> list[ConfidenceIntervention]:
    seen: dict[tuple, ConfidenceIntervention] = {}
    for ivs in groups:
        for iv in ivs:
            seen.setdefault((iv.type, iv.product_id, iv.label), iv)
    return list(seen.values())


def _resolve_profile(user: m.User | None, profile_id: str | None) -> ProfileCtx:
    """Resolve the "shopping for" profile into a scoring context."""
    state = fp_service.load_state(user)
    prof = fp_service.get_profile(state, profile_id)
    if prof is None:
        return ProfileCtx()
    return ProfileCtx(
        id=prof.id,
        name=prof.name or ("You" if prof.is_self else "Them"),
        for_self=bool(prof.is_self),
        fit_map=fp_service.axis_size_map(prof),
    )


def _anonymous_ctx() -> ProfileCtx:
    """An unassigned 'Anyone' gift line — no size opinion, just product signals."""
    return ProfileCtx(id="anyone", name="Anyone", for_self=False, anonymous=True)


def compute(
    db: Session,
    user: m.User | None,
    user_id: str,
    lines: list[CartLine],
    *,
    profile_id: str | None = None,
    sku_health: dict[str, SkuHealth] | None = None,
    fit_flags: dict[str, list[str]] | None = None,
    purchased_brands: set[str] | None = None,
) -> ReturnConfidence:
    """Score intended purchase lines into a Return Confidence. Each line carries
    its OWN recipient (`line.profile_id`), so the cart scores per person and only
    treats duplicates as bracketing when they're for the SAME person."""
    state = fp_service.load_state(user)
    return_rate = float(user.return_rate) if user and user.return_rate is not None else 0.0
    sku_health = sku_health if sku_health is not None else sku_health_map(db)
    fit_flags = fit_flags or {}

    # SELF-only enrichments (own brand history, KEPT-order size inference, owned
    # devices, saved setup) — computed once, lazily, shared across all self lines.
    self_cache: dict = {}

    def _self_enrich() -> dict:
        if not self_cache:
            self_cache["brands"] = (
                purchased_brands if purchased_brands is not None
                else (_purchased_brands(db, user_id) if lines else set())
            )
            self_cache["history"] = _history_fit_map(db, user_id) if lines else {}
            self_cache["owned"] = _owned_devices(db, user_id) if lines else {}
            self_cache["setup"] = fp_service.get_setup(state)
        return self_cache

    ctx_cache: dict[str, ProfileCtx] = {}

    def ctx_for(recipient: str | None) -> ProfileCtx:
        key = (recipient or "anyone").strip() or "anyone"
        if key in ctx_cache:
            return ctx_cache[key]
        if key == "anyone":
            ctx = _anonymous_ctx()
        else:
            prof = fp_service.get_profile(state, key)
            if prof is None:
                ctx = _anonymous_ctx()
            else:
                ctx = ProfileCtx(
                    id=prof.id,
                    name=prof.name or ("You" if prof.is_self else "Them"),
                    for_self=bool(prof.is_self),
                    fit_map=fp_service.axis_size_map(prof),
                )
                if ctx.for_self:
                    en = _self_enrich()
                    for axis, size in en["history"].items():
                        if axis not in ctx.fit_map:
                            ctx.fit_map[axis] = size
                            ctx.inferred_axes.add(axis)
                    ctx.owned = en["owned"]
                    ctx.setup = en["setup"]
        ctx_cache[key] = ctx
        return ctx

    # Group by (product, recipient): "hoodie M for me + hoodie L for Priya" is two
    # gifts (two groups, no bracketing), not a return-bracket.
    groups: dict[tuple[str, str], list[CartLine]] = {}
    order: list[tuple[str, str]] = []
    for ln in lines:
        gkey = (str(ln.product.id), (ln.profile_id or "anyone").strip() or "anyone")
        if gkey not in groups:
            order.append(gkey)
            groups[gkey] = []
        groups[gkey].append(ln)

    items: list[ProductConfidence] = []
    any_self = False
    for gkey in order:
        ctx = ctx_for(gkey[1])
        any_self = any_self or ctx.for_self
        brands = _self_enrich()["brands"] if ctx.for_self else set()
        items.append(_score_product(groups[gkey], ctx, sku_health, fit_flags, brands))

    # The worst line drives the cart, then fold in the silent return-rate penalty
    # (only if at least one line is for the buyer themselves).
    overall = min((it.keep_score for it in items), default=_BASE_SCORE)
    if any_self:
        overall -= _return_rate_penalty(return_rate)
    overall = max(0.05, min(0.99, overall))
    band = _band(overall)

    # Top-level profile fields: the PDP passes an explicit profile_id (one line);
    # the cart is per-line now, so reflect the first line's recipient.
    if profile_id:
        top = ctx_for(profile_id)
    elif order:
        top = ctx_for(order[0][1])
    else:
        top = _resolve_profile(user, None)

    return ReturnConfidence(
        user_id=user_id,
        profile_id=top.id,
        profile_name=top.name,
        for_self=top.for_self,
        keep_score=round(overall, 3),
        confidence_band=band,
        headline=_HEADLINES[band],
        drivers=_merge_drivers(it.drivers for it in items),
        interventions=_merge_interventions(it.interventions for it in items),
        items=items,
    )


def _empty(user: m.User | None, user_id: str, profile_id: str | None) -> ReturnConfidence:
    ctx = _resolve_profile(user, profile_id)
    band = _band(_BASE_SCORE)
    return ReturnConfidence(
        user_id=user_id, profile_id=ctx.id, profile_name=ctx.name, for_self=ctx.for_self,
        keep_score=_BASE_SCORE, confidence_band=band, headline=_HEADLINES[band],
    )


def compute_for_cart(
    db: Session, user_id: str, *, profile_id: str | None = None
) -> ReturnConfidence:
    """Return Confidence for the caller's current server cart (DB-only, no ML)."""
    rows = db.execute(
        select(m.CartItem).where(m.CartItem.user_id == user_id)
    ).scalars().all()
    user = db.get(m.User, user_id)
    if not rows:
        return _empty(user, user_id, profile_id)

    pids = {str(r.product_id) for r in rows}
    products = {
        str(p.id): p
        for p in db.execute(
            select(m.Product).where(m.Product.id.in_(pids))
        ).scalars().all()
    }
    lines = [
        CartLine(
            product=products[str(r.product_id)], size=r.size, variant=r.variant,
            line_id=str(r.id), profile_id=r.profile_id,
        )
        for r in rows
        if str(r.product_id) in products
    ]
    # `profile_id` here only colours the top-level summary; each line is scored
    # for its OWN recipient (line.profile_id).
    return compute(db, user, user_id, lines, profile_id=profile_id)


def compute_for_product(
    db: Session,
    user_id: str,
    product: m.Product,
    size: str | None,
    *,
    profile_id: str | None = None,
    fit_flags: list[str] | None = None,
) -> ReturnConfidence:
    """Return Confidence for a single product/size on the PDP. `fit_flags` are the
    relay-ml fit-flag types (best-effort; prevention never hard-depends on ML)."""
    user = db.get(m.User, user_id)
    ff = {str(product.id): fit_flags} if fit_flags else None
    # On the PDP a missing profile_id means "use the active 'shopping for' profile"
    # (the FitProfileSelector default) — NOT an anonymous gift. Only the cart treats
    # an unassigned line as "Anyone".
    resolved = profile_id or fp_service.load_state(user).active_profile or fp_service.SELF_ID
    return compute(
        db, user, user_id,
        [CartLine(product=product, size=size, profile_id=resolved)],
        profile_id=resolved, fit_flags=ff,
    )
