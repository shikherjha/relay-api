from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

LifeLedgerEventType = Literal[
    # first-life (Layer-1) + reverse-logistics lifecycle
    "PURCHASED", "RETURN_REQUESTED", "PICKED_UP",
    # grading + trust
    "GRADED", "REGRADE_REQUESTED",
    # Path A — hyperlocal intercept
    "RESCUED",
    # Path B — warehouse disposition (Certified Second-Life)
    "REFURBISHED", "RELISTED",
    # other dispositions
    "P2P_LISTED", "P2P_SOLD", "EXCHANGED", "DONATED", "RECYCLED",
]


class LifeLedgerEvent(BaseModel):
    event_type: LifeLedgerEventType
    tx_hash: str | None = None
    passport_hash: str | None = None
    created_at: datetime | None = None
    # Block-explorer link when this event was anchored on a real chain (else None).
    explorer_url: str | None = None


class VerifyResult(BaseModel):
    unit_id: str
    verified: bool
    passport_hash: str | None = None
    on_chain_hash: str | None = None
    tx_hash: str | None = None
    # True when the latest anchor is a real on-chain tx; `network` names the chain
    # (e.g. "Polygon Amoy") and `explorer_url` deep-links the anchoring tx.
    on_chain: bool = False
    network: str | None = None
    explorer_url: str | None = None
    events: list[LifeLedgerEvent] = Field(default_factory=list)
    # Product context + imagery so the provenance page doubles as the product
    # page: the catalogue image plus every user-uploaded condition shot (from
    # the return grading and any resale/relist), de-duplicated.
    title: str | None = None
    category: str | None = None
    vertical: str | None = None
    image_url: str | None = None
    grade: str | None = None
    media_urls: list[str] = Field(default_factory=list)
