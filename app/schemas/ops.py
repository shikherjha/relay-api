from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.rescue import RescueListing


class HighReturnSku(BaseModel):
    sku: str
    title: str | None = None
    return_count: int
    total_sold: int | None = None
    return_rate: float = Field(..., ge=0, le=1)
    dominant_reason: str | None = None
    recommendation: str | None = None  # e.g. "update product photos"


class ChainDepthRow(BaseModel):
    unit_id: str
    transfer_count: int
    forced_channel: str | None = None  # set when >= cap (refurb/donate/recycle)


class OpsImpact(BaseModel):
    total_co2_saved_kg: float = 0.0
    rescued_units: int = 0
    updated_at: datetime | None = None


class OpsRescueLive(BaseModel):
    listings: list[RescueListing] = Field(default_factory=list)
