from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.hashing import passport_hash as compute_hash
from app.core.ids import to_uuid
from app.db.session import get_db
from app.models import entities as m
from app.schemas.lifeledger import LifeLedgerEvent, VerifyResult

router = APIRouter(prefix="/lifeledger", tags=["lifeledger"])


@router.get("/{unit_id}/verify", response_model=VerifyResult)
def verify(unit_id: str, db: Session = Depends(get_db)) -> VerifyResult:
    uid = to_uuid(unit_id, what="unit id")
    events = db.execute(
        select(m.LifeLedgerEvent).where(m.LifeLedgerEvent.unit_id == uid)
        .order_by(desc(m.LifeLedgerEvent.created_at))
    ).scalars().all()
    passport = db.execute(
        select(m.ConditionPassport).where(m.ConditionPassport.unit_id == uid)
        .order_by(desc(m.ConditionPassport.graded_at))
    ).scalars().first()

    # Tamper-evidence: recompute the hash from the stored passport JSON and
    # compare it to what was anchored on-chain (mock or real).
    recomputed = compute_hash(passport.passport) if passport else None
    anchored = next((e for e in events if e.passport_hash), None)
    on_chain_hash = anchored.passport_hash if anchored else None
    tx_hash = anchored.tx_hash if anchored else None
    verified = bool(recomputed and on_chain_hash and recomputed == on_chain_hash)

    return VerifyResult(
        unit_id=unit_id, verified=verified,
        passport_hash=recomputed, on_chain_hash=on_chain_hash, tx_hash=tx_hash,
        events=[
            LifeLedgerEvent(
                event_type=e.event_type, tx_hash=e.tx_hash,
                passport_hash=e.passport_hash, created_at=e.created_at,
            )
            for e in events
        ],
    )
