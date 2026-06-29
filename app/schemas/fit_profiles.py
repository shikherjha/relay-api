"""Fit Profiles — "who are you shopping for?" (plan.md §21.1, Phase 1).

A True Fit-style personalization layer for Return Confidence. Each shopper owns
a small set of profiles — `You` (self) plus the people they buy for (partner,
child, parent…) — modelled like Google Photos people groups. A profile holds
**wardrobe anchors** (the most reliable size signal: "size you own in a known
brand") rather than raw body measurements, plus an optional fit preference.

Selecting a profile drives the size recommendation; picking a non-self profile is
how "buying for someone else" is handled — personal-to-the-buyer signals (own
purchase history, own return rate) are suppressed for that profile.

Storage is no-migration: the whole state lives in `users.fit_profile` (JSONB)
alongside the legacy flat axis keys, which keep mirroring the self profile so
`matching.py` / `bracketing.py` continue to work unchanged. See
`services.fit_profiles`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# A profile carries one anchor per size axis (mirrors return_confidence._FIT_AXIS).
FitAxis = Literal["tops", "bottoms", "shoes"]
Relationship = Literal["self", "partner", "child", "parent", "friend", "other"]


class SizeAnchor(BaseModel):
    """A wardrobe anchor: a size this person wears, ideally in a known brand.

    Brand matters because sizing isn't standardized — "M at Uniqlo" is a far
    stronger signal than a bare "M". Brand is optional so the prompt never blocks.
    """

    size: str
    brand: str | None = None


class FitProfileEntry(BaseModel):
    id: str
    name: str
    relationship: Relationship = "other"
    is_self: bool = False
    # axis (tops/bottoms/shoes) -> wardrobe anchor.
    anchors: dict[str, SizeAnchor] = Field(default_factory=dict)
    # free-form preferences, e.g. {"fit": "slim|regular|relaxed"}.
    prefs: dict[str, str] = Field(default_factory=dict)
    # Electronics "setup" (§21.1 Phase 3) — the device-side analog of wardrobe
    # anchors. e.g. {"phone": "ios", "laptop": "usb-c"}. Powers the personalized
    # compatibility verdict ("Works with your iPhone ✓"). Self profile only.
    setup: dict[str, str] = Field(default_factory=dict)


class FitProfilesState(BaseModel):
    active_profile: str = "self"
    profiles: list[FitProfileEntry] = Field(default_factory=list)


class ProfileUpsert(BaseModel):
    """Create (no id) or update (id set) a profile. The self profile is editable
    but its relationship/identity flags are protected server-side."""

    id: str | None = None
    name: str
    relationship: Relationship = "other"
    anchors: dict[str, SizeAnchor] = Field(default_factory=dict)
    prefs: dict[str, str] = Field(default_factory=dict)


class SetActiveProfile(BaseModel):
    profile_id: str


class SetupUpsert(BaseModel):
    """Set the buyer's device setup (electronics compatibility), e.g.
    {"phone": "ios", "laptop": "usb-c"}. Stored on the self profile."""

    setup: dict[str, str] = Field(default_factory=dict)
