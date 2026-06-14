from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import current_user_id
from app.core.ids import to_uuid
from app.db.session import get_db
from app.models import entities as m
from app.schemas.users import AccessTier, FitProfile, ImpactEventOut, ImpactWallet
from app.services.rescue import access_tiers, user_tier

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

    now = datetime.now(timezone.utc)
    unlocked = 0.0
    locked = 0.0
    for c in credits:
        unlock_at = c.unlock_at
        if unlock_at is not None and unlock_at.tzinfo is None:
            unlock_at = unlock_at.replace(tzinfo=timezone.utc)
        if unlock_at is None or unlock_at <= now:
            unlocked += float(c.amount)
        else:
            locked += float(c.amount)

    lifetime = round(unlocked + locked, 2)
    threshold = settings.rescue_early_access_credit_threshold

    # Tiered early access: which tier the lifetime credits unlock + the next rung.
    ladder = access_tiers()  # (name, threshold, lead_seconds) ascending
    tiers = [
        AccessTier(
            name=name, threshold=thr,
            early_access_hours=round(secs / 3600, 2),
            unlocked=lifetime >= thr,
        )
        for name, thr, secs in ladder
    ]
    current_tier = user_tier(lifetime)
    next_tier = next((t for t in tiers if not t.unlocked), None)

    return ImpactWallet(
        user_id=user_id,
        total_co2_saved_kg=round(sum(e.co2_saved_kg for e in events), 3),
        credits_balance=round(unlocked, 2),
        locked_credits=round(locked, 2),
        lifetime_credits=lifetime,
        early_access=lifetime >= threshold,
        early_access_threshold=threshold,
        tier=current_tier,
        next_tier=next_tier.name if next_tier else None,
        credits_to_next_tier=round(next_tier.threshold - lifetime, 2) if next_tier else None,
        tiers=tiers,
        events=[
            ImpactEventOut(channel=e.channel, co2_saved_kg=e.co2_saved_kg, created_at=e.created_at)
            for e in events
        ],
    )
