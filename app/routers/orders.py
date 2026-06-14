from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import current_user_id
from app.core.ids import to_uuid
from app.db.session import get_db
from app.models import entities as m
from app.schemas.orders import (
    CheckoutRequest,
    ExchangeRequest,
    ExchangeResult,
    Order,
    OrderItemReturnRequest,
)
from app.schemas.returns import ReturnEvent
from app.services.order_actions import (
    OrderActionError,
    exchange_order_item,
    record_order_item_return,
)
from app.services.orders import list_orders, order_to_schema, place_order

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("/checkout", response_model=Order, status_code=201)
def checkout(
    payload: CheckoutRequest | None = None,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> Order:
    payload = payload or CheckoutRequest()
    try:
        order = place_order(
            db, user_id=to_uuid(user_id, what="user id"),
            items=payload.items, geo=payload.geo, clear_cart=payload.clear_cart,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return order_to_schema(db, order)


@router.get("", response_model=list[Order])
def get_orders(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)) -> list[Order]:
    return list_orders(db, to_uuid(user_id, what="user id"))


@router.post("/items/{order_item_id}/return", response_model=ReturnEvent, status_code=201)
def return_order_item(
    order_item_id: str,
    payload: OrderItemReturnRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> ReturnEvent:
    """Order-line return. ``wrong_item`` is fully gated → a flagged
    return-to-seller record (no grade, no passport/GRADED anchor, no listing)."""
    try:
        row = record_order_item_return(
            db,
            order_item_id=to_uuid(order_item_id, what="order item id"),
            caller_id=to_uuid(user_id, what="user id"),
            reason=payload.reason, pickup_slot=payload.pickup_slot,
        )
    except OrderActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return ReturnEvent(
        id=str(row.id), unit_id=str(row.unit_id),
        order_item_id=str(row.order_item_id) if row.order_item_id else None,
        user_id=str(row.user_id) if row.user_id else None,
        reason_code=row.reason_code, status=row.status,
        pickup_slot=row.pickup_slot, pickup_at=row.pickup_at, created_at=row.created_at,
    )


@router.post("/items/{order_item_id}/exchange", response_model=ExchangeResult)
def exchange_item(
    order_item_id: str,
    payload: ExchangeRequest | None = None,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> ExchangeResult:
    """In-window exchange (no ML grading). The pristine returned unit is
    auto-listed on Path-A rescue at original/minimal discount until pickup."""
    payload = payload or ExchangeRequest()
    try:
        return exchange_order_item(
            db,
            order_item_id=to_uuid(order_item_id, what="order item id"),
            caller_id=to_uuid(user_id, what="user id"),
            new_size=payload.new_size, new_variant=payload.new_variant,
            pickup_slot=payload.pickup_slot,
        )
    except OrderActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/{order_id}", response_model=Order)
def get_order(
    order_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> Order:
    order = db.get(m.Order, to_uuid(order_id, what="order id"))
    if order is None or str(order.user_id) != str(to_uuid(user_id, what="user id")):
        raise HTTPException(status_code=404, detail="order not found")
    return order_to_schema(db, order)
