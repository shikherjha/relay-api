from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import Geo
from app.schemas.ml import ConditionPassport, DispositionChannel

ReturnReason = Literal[
    "too_small", "too_large", "fit", "defective",
    "not_as_described", "changed_mind", "wrong_item", "other",
]


class DemandSignal(BaseModel):
    """Open-wish demand fed into disposition scoring (demand-weighted routing)."""

    open_wish_count: int = Field(default=0, ge=0)
    demand_score: float = Field(default=0.0, ge=0, description="Σ wish_score × geo_decay")


class DispositionRequest(BaseModel):
    unit_id: str
    passport: ConditionPassport
    return_reason: ReturnReason
    user_id: str | None = None
    geo: Geo | None = None
    demand: DemandSignal | None = None


class DispositionResponse(BaseModel):
    channel: DispositionChannel
    score: float = Field(..., ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    guardrails_applied: list[str] = Field(default_factory=list)
    net_co2_saved_kg: float | None = None
