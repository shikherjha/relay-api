"""Fit Profiles store — read / migrate / resolve (plan.md §21.1, Phase 1).

The whole profiles state is persisted in `users.fit_profile` (JSONB) with NO
migration. To stay backward-compatible with every existing reader
(`matching._fit_confidence`, `bracketing.detect`, the legacy
`GET /users/me/fit-profile`), the stored dict keeps the flat axis keys
(`{"tops": "M", "bottoms": "32"}`) mirroring the **self** profile, and adds two
keys alongside:

    {
      "tops": "M", "bottoms": "32",          # legacy mirror of the self profile
      "active_profile": "self",
      "profiles": [ {id, name, anchors, …}, … ]
    }

`load_state` reconstructs the typed state from `profiles` when present, else
treats a flat legacy dict as a single self profile.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import entities as m
from app.schemas.fit_profiles import (
    FitProfileEntry,
    FitProfilesState,
    ProfileUpsert,
    SizeAnchor,
)

SELF_ID = "self"
_AXES = ("tops", "bottoms", "shoes")


def _legacy_self(raw: dict) -> FitProfileEntry:
    """Wrap a flat legacy `{axis: size}` map into the self profile."""
    anchors: dict[str, SizeAnchor] = {}
    for axis in _AXES:
        v = raw.get(axis)
        if isinstance(v, str) and v:
            anchors[axis] = SizeAnchor(size=v)
    return FitProfileEntry(
        id=SELF_ID, name="You", relationship="self", is_self=True, anchors=anchors,
    )


def load_state(user: m.User | None) -> FitProfilesState:
    """Typed profiles state for a user (reconstructs/migrates as needed)."""
    raw = user.fit_profile if user and isinstance(user.fit_profile, dict) else {}
    raw = raw or {}

    profiles_raw = raw.get("profiles")
    if isinstance(profiles_raw, list) and profiles_raw:
        entries: list[FitProfileEntry] = []
        for p in profiles_raw:
            try:
                entries.append(FitProfileEntry.model_validate(p))
            except Exception:  # noqa: BLE001 - skip a corrupt entry, never 500
                continue
        if not entries:
            entries = [_legacy_self(raw)]
        if not any(e.is_self for e in entries):
            entries.insert(0, _legacy_self(raw))
        ids = {e.id for e in entries}
        active = raw.get("active_profile")
        if active not in ids:
            active = next((e.id for e in entries if e.is_self), entries[0].id)
        return FitProfilesState(active_profile=active, profiles=entries)

    # Legacy flat dict (or empty) → a single self profile.
    return FitProfilesState(active_profile=SELF_ID, profiles=[_legacy_self(raw)])


def get_profile(state: FitProfilesState, profile_id: str | None) -> FitProfileEntry | None:
    """Resolve a profile id (falling back to active → self → first)."""
    pid = profile_id or state.active_profile
    by_id = {p.id: p for p in state.profiles}
    if pid in by_id:
        return by_id[pid]
    self_p = next((p for p in state.profiles if p.is_self), None)
    return self_p or (state.profiles[0] if state.profiles else None)


def axis_size_map(profile: FitProfileEntry | None) -> dict[str, str]:
    """Flat `{axis: size}` view of a profile's anchors (what the scorer consumes)."""
    if profile is None:
        return {}
    return {axis: a.size for axis, a in profile.anchors.items() if a and a.size}


def get_setup(state: FitProfilesState) -> dict[str, str]:
    """The buyer's saved device setup (electronics compatibility), self profile."""
    self_p = next((p for p in state.profiles if p.is_self), None)
    return dict(self_p.setup) if self_p and self_p.setup else {}


def set_setup(db: Session, user: m.User, setup: dict[str, str]) -> FitProfilesState:
    """Merge device-setup keys onto the self profile (electronics §21.1)."""
    state = load_state(user)
    self_p = next((p for p in state.profiles if p.is_self), None)
    if self_p is None:
        self_p = FitProfileEntry(id=SELF_ID, name="You", relationship="self", is_self=True)
        state.profiles.insert(0, self_p)
    merged = dict(self_p.setup)
    merged.update({k: v for k, v in (setup or {}).items() if v})
    self_p.setup = merged
    return save_state(db, user, state)


def _to_raw(state: FitProfilesState) -> dict:
    """Serialize back to the hybrid JSONB shape (legacy mirror + profiles)."""
    self_p = next((p for p in state.profiles if p.is_self), None)
    raw: dict = dict(axis_size_map(self_p))  # legacy flat keys mirror the self profile
    raw["active_profile"] = state.active_profile
    raw["profiles"] = [p.model_dump() for p in state.profiles]
    return raw


def save_state(db: Session, user: m.User, state: FitProfilesState) -> FitProfilesState:
    user.fit_profile = _to_raw(state)
    db.add(user)
    db.commit()
    return state


def upsert_profile(db: Session, user: m.User, payload: ProfileUpsert) -> FitProfilesState:
    """Create (no id) or update (id set) a profile. Raises KeyError on unknown id."""
    state = load_state(user)
    if payload.id:
        prof = next((p for p in state.profiles if p.id == payload.id), None)
        if prof is None:
            raise KeyError(payload.id)
        prof.name = payload.name or prof.name
        prof.anchors = payload.anchors
        prof.prefs = payload.prefs
        if not prof.is_self:  # the self profile's identity flags are protected
            prof.relationship = payload.relationship
    else:
        new = FitProfileEntry(
            id=uuid.uuid4().hex[:8],
            name=payload.name,
            relationship=payload.relationship if payload.relationship != "self" else "other",
            is_self=False,
            anchors=payload.anchors,
            prefs=payload.prefs,
        )
        state.profiles.append(new)
    return save_state(db, user, state)


def delete_profile(db: Session, user: m.User, profile_id: str) -> FitProfilesState:
    """Remove a non-self profile. Raises ValueError for self/unknown."""
    state = load_state(user)
    prof = next((p for p in state.profiles if p.id == profile_id), None)
    if prof is None or prof.is_self:
        raise ValueError("cannot delete this profile")
    state.profiles = [p for p in state.profiles if p.id != profile_id]
    if state.active_profile == profile_id:
        state.active_profile = SELF_ID
    return save_state(db, user, state)


def set_active(db: Session, user: m.User, profile_id: str) -> FitProfilesState:
    """Set the active "shopping for" profile. Raises KeyError on unknown id."""
    state = load_state(user)
    if profile_id not in {p.id for p in state.profiles}:
        raise KeyError(profile_id)
    state.active_profile = profile_id
    return save_state(db, user, state)
