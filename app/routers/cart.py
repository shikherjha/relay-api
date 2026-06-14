from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import current_user_id
from app.schemas.cart import Cart, CartItem, CartItemCreate

router = APIRouter(prefix="/cart", tags=["cart"])

# Bracketing fires at >= 3 distinct size/variant of the same product (strict).
BRACKETING_THRESHOLD = 3


@router.get("", response_model=Cart)
def get_cart(user_id: str = Depends(current_user_id)) -> Cart:
    # Step 3: load cart_items, group by product_id, compute bracketing flags.
    return Cart(user_id=user_id, items=[], bracketing=[])


@router.post("", response_model=CartItem, status_code=201)
def add_to_cart(payload: CartItemCreate, user_id: str = Depends(current_user_id)) -> CartItem:
    return CartItem(id="stub", **payload.model_dump())
