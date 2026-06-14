from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

LifeLedgerEventType = Literal[
    "GRADED", "RESCUED", "P2P_LISTED", "P2P_SOLD",
    "EXCHANGED", "DONATED", "RECYCLED", "REGRADE_REQUESTED",
]


class LifeLedgerEvent(BaseModel):
    event_type: LifeLedgerEventType
    tx_hash: str | None = None
    passport_hash: str | None = None
    created_at: datetime | None = None


class VerifyResult(BaseModel):
    unit_id: str
    verified: bool
    passport_hash: str | None = None
    on_chain_hash: str | None = None
    events: list[LifeLedgerEvent] = Field(default_factory=list)
