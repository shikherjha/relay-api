"""Return Confidence schemas (plan.md §21.1).

A non-punitive purchase-keep signal for cart + PDP. `drivers` explain *why* the
score moved (risk-side detail Ops can also read); `interventions` are the
customer-positive next actions (the UI must surface an action, not just a
warning). Customer-facing copy is confidence-building, never "you are likely to
return this".
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["low", "medium", "high"]
ConfidenceBand = Literal["high", "medium", "low"]


class ConfidenceDriver(BaseModel):
    type: str  # bracketing | duplicate_variant | sku_return_health | fit_confidence | size_uncertainty | size_mismatch | fit_signal | new_brand
    label: str
    severity: Severity = "low"
    # True when this signal RAISES keep-confidence (e.g. matches the buyer's usual
    # size). Lets the UI render it as reassurance instead of a risk chip.
    positive: bool = False


class ConfidenceIntervention(BaseModel):
    type: str  # size_recommendation | fit_review | fit_profile | compatibility_check | exchange_assurance
    label: str
    # Machine-readable action the UI can wire up (e.g. drop the spare sizes).
    action: str | None = None  # remove_extra_sizes | add_fit_profile | review_fit | review_compatibility
    product_id: str | None = None
    suggested_size: str | None = None
    # Optional checklist items (electronics "fit-for-purpose": confirm compatibility,
    # in-box contents, key specs). Rendered as bullets on the PDP.
    items: list[str] = Field(default_factory=list)


class ProductConfidence(BaseModel):
    product_id: str
    title: str | None = None
    size: str | None = None
    # The cart line(s) this scored group covers. A product split across recipients
    # (hoodie M for you, L for Priya) yields one entry per (product, recipient), so
    # the cart UI maps each line to its own nudge.
    line_ids: list[str] = Field(default_factory=list)
    # Who this line is for (per-line Fit Profile). "anyone" = unassigned gift.
    profile_id: str = "self"
    profile_name: str = "You"
    for_self: bool = True
    keep_score: float = Field(..., ge=0, le=1)
    confidence_band: ConfidenceBand
    # The single best size for the active profile (drives the Amazon-native PDP
    # line "Recommended for <profile>: <size>"), with a short, friendly reason.
    recommended_size: str | None = None
    recommended_reason: str | None = None
    # Electronics "what people actually returned this for" preempt — the SKU's
    # approximate RETURN RATE (share of buyers who return it, 0–1) plus the
    # dominant return reason as soft context. We surface the return rate (not the
    # defect-share) so the copy reads as honest transparency, not alarm.
    return_reason: str | None = None
    return_reason_share: float | None = None
    return_rate: float | None = None
    drivers: list[ConfidenceDriver] = Field(default_factory=list)
    interventions: list[ConfidenceIntervention] = Field(default_factory=list)


class ReturnConfidence(BaseModel):
    user_id: str
    # Which Fit Profile this was scored for ("who are you shopping for?"). When
    # `for_self` is False the size signals are that person's, and personal-to-the-
    # buyer signals (own history / return rate) are intentionally suppressed.
    profile_id: str = "self"
    profile_name: str = "You"
    for_self: bool = True
    keep_score: float = Field(..., ge=0, le=1)
    confidence_band: ConfidenceBand
    # Confidence-building headline for the shopper (never risk-worded).
    headline: str
    drivers: list[ConfidenceDriver] = Field(default_factory=list)
    interventions: list[ConfidenceIntervention] = Field(default_factory=list)
    # Per-product breakdown (one entry per distinct product in the cart, or a
    # single entry for the PDP path).
    items: list[ProductConfidence] = Field(default_factory=list)
