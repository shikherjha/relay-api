from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.clients.engine_client import EngineClient
from app.clients.ml_client import MLClient
from app.core.deps import current_user_id, engine_client, ml_client
from app.core.ids import to_uuid
from app.db.session import get_db
from app.models import entities as m
from app.schemas.disposition import DispositionRequest, DispositionResponse
from app.schemas.ml import ConditionPassport
from app.schemas.returns import MediaAccepted, ReturnCreate, ReturnEvent
from app.schemas.common import Geo
from app.services.disposition import build_demand_signal, record_outcome
from app.services.grading import grade_and_store

router = APIRouter(prefix="/returns", tags=["returns"])


def _load_return(db: Session, return_id: str) -> m.ReturnEvent:
    row = db.get(m.ReturnEvent, to_uuid(return_id, what="return id"))
    if row is None:
        raise HTTPException(status_code=404, detail="return not found")
    return row


@router.post("", response_model=ReturnEvent, status_code=201)
def create_return(
    payload: ReturnCreate,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> ReturnEvent:
    unit = db.get(m.ProductUnit, to_uuid(payload.unit_id, what="unit id"))
    if unit is None:
        raise HTTPException(status_code=404, detail="unit not found")
    row = m.ReturnEvent(
        unit_id=unit.id,
        user_id=to_uuid(payload.user_id or user_id, what="user id"),
        reason_code=payload.reason_code,
        status="initiated",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return ReturnEvent(
        id=str(row.id), unit_id=str(row.unit_id),
        user_id=str(row.user_id) if row.user_id else None,
        reason_code=row.reason_code, status=row.status, created_at=row.created_at,
    )


@router.post("/{return_id}/media", response_model=MediaAccepted, status_code=202)
async def upload_media(
    return_id: str,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    ml: MLClient = Depends(ml_client),
) -> MediaAccepted:
    return_event = _load_return(db, return_id)
    unit = db.get(m.ProductUnit, return_event.unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="unit not found")

    first = files[0]
    image = await first.read()
    # Synchronous grade for demo determinism; swap to grade_return_task.delay() for async.
    row = grade_and_store(
        db, ml, return_event=return_event, unit=unit, image=image, filename=first.filename or "upload",
    )
    return MediaAccepted(
        job_id=f"job-{return_id}", status="graded",
        passport_id=str(row.id), media_hashes=row.passport.get("media_hashes", []),
    )


@router.get("/{return_id}/passport", response_model=ConditionPassport)
def get_passport(return_id: str, db: Session = Depends(get_db)) -> ConditionPassport:
    return_event = _load_return(db, return_id)
    row = db.execute(
        select(m.ConditionPassport)
        .where(m.ConditionPassport.return_id == return_event.id)
        .order_by(desc(m.ConditionPassport.graded_at))
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="passport not found (grade media first)")
    return ConditionPassport.model_validate(row.passport)


@router.post("/{return_id}/disposition", response_model=DispositionResponse)
def compute_disposition(
    return_id: str,
    db: Session = Depends(get_db),
    engine: EngineClient = Depends(engine_client),
) -> DispositionResponse:
    return_event = _load_return(db, return_id)
    unit = db.get(m.ProductUnit, return_event.unit_id)
    passport_row = db.execute(
        select(m.ConditionPassport)
        .where(m.ConditionPassport.return_id == return_event.id)
        .order_by(desc(m.ConditionPassport.graded_at))
    ).scalars().first()
    if unit is None or passport_row is None:
        raise HTTPException(status_code=409, detail="grade the return before disposition")

    passport = ConditionPassport.model_validate(passport_row.passport)
    demand = build_demand_signal(db, category=passport.category or "other", unit=unit)
    geo = Geo(lat=unit.geo_lat, lng=unit.geo_lng) if unit.geo_lat is not None else None

    # Exchange-first availability: another in-stock unit of the same product.
    exchange_available = db.execute(
        select(m.ProductUnit.id)
        .where(m.ProductUnit.product_id == unit.product_id)
        .where(m.ProductUnit.id != unit.id)
        .where(m.ProductUnit.status == "in_stock")
        .limit(1)
    ).first() is not None

    req = DispositionRequest(
        unit_id=str(unit.id), passport=passport,
        return_reason=return_event.reason_code, user_id=str(return_event.user_id) if return_event.user_id else None,
        geo=geo, demand=demand,
        transfer_count=unit.transfer_count, exchange_available=exchange_available,
    )
    decision = engine.score_disposition(req)
    record_outcome(
        db, user_id=return_event.user_id, unit=unit, decision=decision,
        passport_hash=passport_row.passport_hash,
    )
    return decision
