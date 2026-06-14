from __future__ import annotations

from collections import Counter, defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import entities as m
from app.schemas.ops import ChainDepthRow, HighReturnSku, OpsImpact, OpsRescueLive
from app.schemas.rescue import RescueListing
from app.services.rescue import current_discount

router = APIRouter(prefix="/ops", tags=["ops"])

# Reason -> proactive catalog recommendation (seller-side return signals).
_REASON_FIX = {
    "not_as_described": "review listing copy + product photos",
    "too_small": "add a size-up note / update size chart",
    "too_large": "add a size-down note / update size chart",
    "fit": "update size chart + fit guidance",
    "defective": "audit supplier QC for this SKU",
}


@router.get("/high-return-skus", response_model=list[HighReturnSku])
def high_return_skus(db: Session = Depends(get_db)) -> list[HighReturnSku]:
    rows = db.execute(
        select(m.Product.sku, m.Product.title, m.ReturnEvent.reason_code)
        .join(m.ProductUnit, m.ProductUnit.id == m.ReturnEvent.unit_id)
        .join(m.Product, m.Product.id == m.ProductUnit.product_id)
    ).all()

    counts: dict[str, int] = defaultdict(int)
    titles: dict[str, str] = {}
    reasons: dict[str, Counter] = defaultdict(Counter)
    for sku, title, reason in rows:
        counts[sku] += 1
        titles[sku] = title
        reasons[sku][reason] += 1

    out: list[HighReturnSku] = []
    for sku, n in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        dominant = reasons[sku].most_common(1)[0][0] if reasons[sku] else None
        out.append(HighReturnSku(
            sku=sku, title=titles.get(sku), return_count=n, return_rate=min(n / 10.0, 1.0),
            dominant_reason=dominant,
            recommendation=_REASON_FIX.get(dominant) if dominant else None,
        ))
    return out


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
