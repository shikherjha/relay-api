from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import current_user_id
from app.db.session import get_db
from app.models import entities as m
from app.schemas.cart import Cart, CartItem, CartItemCreate
from app.services.bracketing import detect

router = APIRouter(prefix="/cart", tags=["cart"])


@router.get("", response_model=Cart)
def get_cart(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)) -> Cart:
    rows = db.execute(
        select(m.CartItem).where(m.CartItem.user_id == user_id)
    ).scalars().all()
    user = db.get(m.User, user_id)
    fit_profile = user.fit_profile if user else None

    items = [
        CartItem(
            id=str(r.id), product_id=str(r.product_id), sku=r.sku,
            size=r.size, variant=r.variant, qty=r.qty,
        )
        for r in rows
    ]
    return Cart(user_id=user_id, items=items, bracketing=detect(rows, fit_profile))


@router.post("", response_model=CartItem, status_code=201)
def add_to_cart(
    payload: CartItemCreate,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> CartItem:
    row = m.CartItem(
        user_id=user_id, product_id=payload.product_id, sku=payload.sku,
        size=payload.size, variant=payload.variant, qty=payload.qty,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return CartItem(
        id=str(row.id), product_id=str(row.product_id), sku=row.sku,
        size=row.size, variant=row.variant, qty=row.qty,
    )
