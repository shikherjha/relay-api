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
from app.schemas.users import (
    AccessTier,
    FitProfile,
    ImpactEventOut,
    ImpactWallet,
    ResaleTracking,
    ReturnTracking,
)
from app.services.rescue import access_tiers, user_tier

router = APIRouter(prefix="/users/me", tags=["users"])


def _latest_passport_for_return(db: Session, return_id, unit_id):
    """Passport for this return (preferred), else the latest for the unit."""
    row = db.execute(
        select(m.ConditionPassport).where(m.ConditionPassport.return_id == return_id)
        .order_by(m.ConditionPassport.graded_at.desc())
    ).scalars().first()
    if row is not None:
        return row
    return db.execute(
        select(m.ConditionPassport).where(m.ConditionPassport.unit_id == unit_id)
        .order_by(m.ConditionPassport.graded_at.desc())
    ).scalars().first()


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


@router.get("/returns", response_model=list[ReturnTracking])
def my_returns(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)) -> list[ReturnTracking]:
    """Track the caller's returns end-to-end: status, the AI condition grade, and
    where the item is headed next (rescue feed / Second Life) so a confirmed
    return never just disappears."""
    uid = to_uuid(user_id, what="user id")
    rows = db.execute(
        select(m.ReturnEvent).where(m.ReturnEvent.user_id == uid)
        .order_by(m.ReturnEvent.created_at.desc())
    ).scalars().all()

    out: list[ReturnTracking] = []
    for r in rows:
        unit = db.get(m.ProductUnit, r.unit_id)
        product = db.get(m.Product, unit.product_id) if unit is not None else None
        passport = _latest_passport_for_return(db, r.id, r.unit_id)
        grade = None
        media_urls: list[str] = []
        disposition = None
        if passport is not None and isinstance(passport.passport, dict):
            grade = passport.passport.get("grade")
            media_urls = list(passport.passport.get("media_urls") or [])
            disposition = passport.passport.get("disposition_hint")

        rescue_listed = db.execute(
            select(m.RescueListing.id).where(m.RescueListing.unit_id == r.unit_id)
            .where(m.RescueListing.status == "active").limit(1)
        ).first() is not None
        second_life_listed = db.execute(
            select(m.ResaleListing.id).where(m.ResaleListing.unit_id == r.unit_id)
            .where(m.ResaleListing.status == "active").limit(1)
        ).first() is not None
        if rescue_listed:
            disposition = "rescue"
        elif second_life_listed:
            disposition = "p2p_resale"

        out.append(ReturnTracking(
            return_id=str(r.id), unit_id=str(r.unit_id),
            order_item_id=str(r.order_item_id) if r.order_item_id else None,
            title=product.title if product else None,
            category=product.category if product else None,
            vertical=product.vertical if product else None,
            image_url=product.image_url if product else None,
            reason_code=r.reason_code, status=r.status, created_at=r.created_at,
            pickup_slot=r.pickup_slot, grade=grade, media_urls=media_urls,
            disposition_channel=disposition,
            rescue_listed=rescue_listed, second_life_listed=second_life_listed,
        ))
    return out


@router.get("/resales", response_model=list[ResaleTracking])
def my_resales(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)) -> list[ResaleTracking]:
    """The caller's own Second-Life resale listings (p2p) with live status, so a
    buyer can follow a unit they've put up for resale."""
    uid = to_uuid(user_id, what="user id")
    rows = db.execute(
        select(m.ResaleListing).where(m.ResaleListing.lister_id == uid)
        .where(m.ResaleListing.source == "p2p")
        .order_by(m.ResaleListing.created_at.desc())
    ).scalars().all()

    out: list[ResaleTracking] = []
    for row in rows:
        unit = db.get(m.ProductUnit, row.unit_id)
        product = db.get(m.Product, unit.product_id) if unit is not None else None
        out.append(ResaleTracking(
            listing_id=str(row.id), unit_id=str(row.unit_id),
            title=product.title if product else None,
            category=product.category if product else None,
            vertical=product.vertical if product else None,
            image_url=product.image_url if product else None,
            source=row.source,
            resale_grade=row.resale_grade,
            list_price=float(row.list_price) if row.list_price is not None else None,
            price_min=float(row.price_min) if row.price_min is not None else None,
            price_max=float(row.price_max) if row.price_max is not None else None,
            status=row.status, escrow_status=row.escrow_status, age_days=row.age_days,
            created_at=row.created_at, media_urls=list(row.media_urls or []),
        ))
    return out
