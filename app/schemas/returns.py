from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.disposition import ReturnReason


class ReturnCreate(BaseModel):
    unit_id: str
    user_id: str | None = None
    reason_code: ReturnReason


class ReturnEvent(BaseModel):
    id: str
    unit_id: str
    user_id: str | None = None
    reason_code: str
    status: str
    created_at: datetime | None = None


class MediaAccepted(BaseModel):
    job_id: str
    status: Literal["queued"] = "queued"
    media_hashes: list[str] = []
