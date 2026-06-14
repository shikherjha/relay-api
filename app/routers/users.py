from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import current_user_id
from app.schemas.users import FitProfile, ImpactWallet

router = APIRouter(prefix="/users/me", tags=["users"])


@router.get("/fit-profile", response_model=FitProfile)
def get_fit_profile(user_id: str = Depends(current_user_id)) -> FitProfile:
    return FitProfile(user_id=user_id, return_rate=0.0, fit_profile={})


@router.get("/impact", response_model=ImpactWallet)
def get_impact(user_id: str = Depends(current_user_id)) -> ImpactWallet:
    return ImpactWallet(user_id=user_id, total_co2_saved_kg=0.0, credits_balance=0.0, events=[])
