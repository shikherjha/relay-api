from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import current_user_id
from app.db.session import get_db
from app.models import entities as m
from app.schemas.cart import Cart, CartItem, CartItemCreate, CartItemPatch
from app.schemas.return_confidence import ReturnConfidence
from app.services.bracketing import detect
from app.services.return_confidence import compute_for_cart

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
            size=r.size, variant=r.variant, qty=r.qty, profile_id=r.profile_id,
        )
        for r in rows
    ]
    return Cart(user_id=user_id, items=items, bracketing=detect(rows, fit_profile))


@router.get("/return-confidence", response_model=ReturnConfidence)
def cart_return_confidence(
    profile_id: str | None = Query(default=None),
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> ReturnConfidence:
    """Non-punitive purchase-keep signal for the current cart (plan.md §21.1):
    `keep_score`, a confidence band, the risk drivers, and the customer-positive
    interventions that help the shopper buy one item with confidence. `profile_id`
    scores it for whoever they're shopping for (Fit Profiles)."""
    return compute_for_cart(db, user_id, profile_id=profile_id)


@router.post("", response_model=CartItem, status_code=201)
def add_to_cart(
    payload: CartItemCreate,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> CartItem:
    row = m.CartItem(
        user_id=user_id, product_id=payload.product_id, sku=payload.sku,
        size=payload.size, variant=payload.variant, qty=payload.qty,
        profile_id=payload.profile_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return CartItem(
        id=str(row.id), product_id=str(row.product_id), sku=row.sku,
        size=row.size, variant=row.variant, qty=row.qty, profile_id=row.profile_id,
    )


@router.patch("/{item_id}", response_model=CartItem)
def update_cart_item(
    item_id: str,
    payload: CartItemPatch,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> CartItem:
    """Reassign a cart line: change its size or who it's *for* (Fit Profile).
    `clear_profile` sets the recipient back to "Anyone" (unassigned)."""
    row = db.get(m.CartItem, item_id)
    if row is None or str(row.user_id) != str(user_id):
        raise HTTPException(status_code=404, detail="cart item not found")
    if payload.size is not None:
        row.size = payload.size
    if payload.clear_profile:
        row.profile_id = None
    elif payload.profile_id is not None:
        row.profile_id = payload.profile_id
    db.commit()
    db.refresh(row)
    return CartItem(
        id=str(row.id), product_id=str(row.product_id), sku=row.sku,
        size=row.size, variant=row.variant, qty=row.qty, profile_id=row.profile_id,
    )


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def remove_from_cart(
    item_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> Response:
    row = db.get(m.CartItem, item_id)
    if row is None or str(row.user_id) != str(user_id):
        raise HTTPException(status_code=404, detail="cart item not found")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
