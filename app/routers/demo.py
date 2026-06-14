from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.common import StatusResponse
from app.services.seed import seed_all

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post("/reset", response_model=StatusResponse)
def demo_reset(db: Session = Depends(get_db)) -> StatusResponse:
    counts = seed_all(db)
    return StatusResponse(status="ok", detail=counts)
