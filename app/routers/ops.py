from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import entities as m
from app.schemas.ops import ChainDepthRow, HighReturnSku, OpsImpact, OpsRescueLive
from app.schemas.rescue import RescueListing
from app.services.rescue import current_discount
from app.services.return_signals import aggregate_sku_health

router = APIRouter(prefix="/ops", tags=["ops"])


def _aggregate_skus(db: Session) -> list[HighReturnSku]:
    # Shared with the customer-facing Return Confidence layer (one story).
    return [
        HighReturnSku(
            sku=h.sku, title=h.title, return_count=h.return_count,
            return_rate=h.return_rate, dominant_reason=h.dominant_reason,
            recommendation=h.recommendation,
        )
        for h in aggregate_sku_health(db)
    ]


@router.get("/high-return-skus", response_model=list[HighReturnSku])
def high_return_skus(db: Session = Depends(get_db)) -> list[HighReturnSku]:
    return _aggregate_skus(db)


@router.get("/seller-signals", response_model=list[HighReturnSku])
def seller_signals(db: Session = Depends(get_db)) -> list[HighReturnSku]:
    """Only SKUs that crossed the threshold and have an actionable catalog fix."""
    return [s for s in _aggregate_skus(db) if s.recommendation is not None]


@router.get("/rescue-live", response_model=OpsRescueLive)
def rescue_live(db: Session = Depends(get_db)) -> OpsRescueLive:
    rows = db.execute(
        select(m.RescueListing).where(m.RescueListing.status == "active")
    ).scalars().all()
    listings = [
        RescueListing(
            id=str(r.id), unit_id=str(r.unit_id), discount_pct=current_discount(r),
            base_discount_pct=r.base_discount_pct, current_discount_pct=current_discount(r),
            ttl_seconds=r.ttl_seconds, expires_at=r.expires_at, status=r.status,
        )
        for r in rows
    ]
    return OpsRescueLive(listings=listings)


@router.get("/chain-depth", response_model=list[ChainDepthRow])
def chain_depth(db: Session = Depends(get_db)) -> list[ChainDepthRow]:
    rows = db.execute(
        select(m.ProductUnit).where(m.ProductUnit.transfer_count > 0)
        .order_by(m.ProductUnit.transfer_count.desc())
    ).scalars().all()
    return [
        ChainDepthRow(
            unit_id=str(u.id), transfer_count=u.transfer_count,
            forced_channel="refurb" if u.transfer_count >= 3 else None,
        )
        for u in rows
    ]


@router.get("/impact", response_model=OpsImpact)
def ops_impact(db: Session = Depends(get_db)) -> OpsImpact:
    total = db.execute(select(func.coalesce(func.sum(m.ImpactEvent.co2_saved_kg), 0.0))).scalar_one()
    rescued = db.execute(
        select(func.count()).select_from(m.ImpactEvent).where(m.ImpactEvent.channel == "rescue")
    ).scalar_one()
    return OpsImpact(total_co2_saved_kg=round(float(total), 3), rescued_units=int(rescued))
