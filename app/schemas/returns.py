from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.disposition import ReturnReason


class ReturnCreate(BaseModel):
    # Order-linked is the preferred path (return what you actually bought). The
    # raw unit_id path is retained for legacy/seeded units and direct grading.
    order_item_id: str | None = None
    unit_id: str | None = None
    user_id: str | None = None
    reason_code: ReturnReason
    # Pickup-anchored reverse logistics. The rescue TTL clock only starts once
    # the courier collects the unit (pickup_at), not at return-request time.
    pickup_slot: str | None = None


class ReturnEvent(BaseModel):
    id: str
    unit_id: str
    order_item_id: str | None = None
    user_id: str | None = None
    reason_code: str
    status: str
    pickup_slot: str | None = None
    pickup_at: datetime | None = None
    created_at: datetime | None = None


class MediaAccepted(BaseModel):
    job_id: str
    status: Literal["queued", "graded"] = "queued"
    passport_id: str | None = None
    media_hashes: list[str] = []
    # Absolute S3 URLs of the uploaded return media (empty if S3 not configured).
    media_urls: list[str] = []
