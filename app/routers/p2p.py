from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import current_user_id
from app.core.ids import to_uuid
from app.db.session import get_db
from app.models import entities as m
from app.schemas.p2p import P2PListing, P2PListingCreate

router = APIRouter(prefix="/p2p", tags=["p2p"])


@router.post("/listings", response_model=P2PListing, status_code=201)
def create_listing(
    payload: P2PListingCreate,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> P2PListing:
    unit = db.get(m.ProductUnit, to_uuid(payload.unit_id, what="unit id"))
    if unit is None:
        raise HTTPException(status_code=404, detail="unit not found")

    price = payload.price
    if price is None:
        product = db.get(m.Product, unit.product_id)
        price = float(product.price) * 0.7 if product else 0.0  # default 30% off

    row = m.P2PListing(unit_id=unit.id, seller_id=to_uuid(user_id), price=price)
    db.add(row)
    db.add(m.LifeLedgerEvent(unit_id=unit.id, event_type="P2P_LISTED"))
    db.commit()
    db.refresh(row)
    return P2PListing(
        id=str(row.id), unit_id=str(row.unit_id),
        seller_id=str(row.seller_id) if row.seller_id else None,
        price=float(row.price), status=row.status, escrow_status=row.escrow_status,
    )
