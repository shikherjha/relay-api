from __future__ import annotations

from pydantic import BaseModel, Field


class RepairEventIn(BaseModel):
    description: str
    at: str | None = None


class WarrantyRecord(BaseModel):
    unit_id: str
    months_remaining: int = Field(..., ge=0)
    repair_events: list[dict] = Field(default_factory=list)
