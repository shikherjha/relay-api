"""Deterministic passport hashing for LifeLedger anchoring + verification."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def passport_hash(passport: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON (sorted keys), excluding the hash field itself."""
    payload = {k: v for k, v in passport.items() if k != "passport_hash"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
