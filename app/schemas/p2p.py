from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

EscrowStatus = Literal["none", "held", "released", "refunded"]


class P2PListingCreate(BaseModel):
    unit_id: str
    price: float | None = None


class P2PListing(BaseModel):
    id: str
    unit_id: str
    seller_id: str | None = None
    price: float
    status: str = "listed"
    escrow_status: EscrowStatus = "none"
