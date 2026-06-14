from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import current_user_id
from app.core.ids import to_uuid
from app.db.session import get_db
from app.models import entities as m
from app.schemas.users import FitProfile, ImpactEventOut, ImpactWallet

router = APIRouter(prefix="/users/me", tags=["users"])


@router.get("/fit-profile", response_model=FitProfile)
def get_fit_profile(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)) -> FitProfile:
    user = db.get(m.User, to_uuid(user_id, what="user id"))
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return FitProfile(user_id=str(user.id), return_rate=user.return_rate, fit_profile=user.fit_profile or {})


@router.get("/impact", response_model=ImpactWallet)
def get_impact(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)) -> ImpactWallet:
    uid = to_uuid(user_id, what="user id")
    events = db.execute(
        select(m.ImpactEvent).where(m.ImpactEvent.user_id == uid)
        .order_by(m.ImpactEvent.created_at.desc())
    ).scalars().all()
    credits = db.execute(
        select(m.GreenCreditLedger).where(m.GreenCreditLedger.user_id == uid)
    ).scalars().all()

    return ImpactWallet(
        user_id=user_id,
        total_co2_saved_kg=round(sum(e.co2_saved_kg for e in events), 3),
        credits_balance=round(sum(float(c.amount) for c in credits), 2),
        events=[
            ImpactEventOut(channel=e.channel, co2_saved_kg=e.co2_saved_kg, created_at=e.created_at)
            for e in events
        ],
    )
