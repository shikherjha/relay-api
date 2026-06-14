from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import to_uuid
from app.db.session import get_db
from app.models import entities as m
from app.schemas.warranty import RepairEventIn, WarrantyRecord

router = APIRouter(prefix="/units", tags=["warranty"])


def _load(db: Session, unit_id) -> m.WarrantyRecord:
    row = db.execute(
        select(m.WarrantyRecord).where(m.WarrantyRecord.unit_id == unit_id)
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="no warranty record for unit")
    return row


@router.get("/{unit_id}/warranty", response_model=WarrantyRecord)
def get_warranty(unit_id: str, db: Session = Depends(get_db)) -> WarrantyRecord:
    uid = to_uuid(unit_id, what="unit id")
    row = _load(db, uid)
    return WarrantyRecord(
        unit_id=str(row.unit_id), months_remaining=row.months_remaining,
        repair_events=row.repair_events or [],
    )


@router.post("/{unit_id}/warranty/repairs", response_model=WarrantyRecord, status_code=201)
def add_repair(unit_id: str, payload: RepairEventIn, db: Session = Depends(get_db)) -> WarrantyRecord:
    uid = to_uuid(unit_id, what="unit id")
    row = _load(db, uid)
    events = list(row.repair_events or [])
    events.append(payload.model_dump(exclude_none=True))
    row.repair_events = events
    db.add(m.LifeLedgerEvent(unit_id=uid, event_type="REGRADE_REQUESTED"))
    db.commit()
    db.refresh(row)
    return WarrantyRecord(
        unit_id=str(row.unit_id), months_remaining=row.months_remaining,
        repair_events=row.repair_events or [],
    )
