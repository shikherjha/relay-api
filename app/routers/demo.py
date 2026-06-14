from __future__ import annotations

from fastapi import APIRouter

from app.schemas.common import StatusResponse

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post("/reset", response_model=StatusResponse)
def demo_reset() -> StatusResponse:
    # Step 3 (api-seed): truncate + re-seed demo data (incl. bracketing carts).
    return StatusResponse(status="ok")
