"""Layer-1 (Amazon) checkout + order history (api-orders).

A checkout turns cart/explicit lines into an Order with OrderItems, each bound to
a freshly-minted ProductUnit owned by the buyer (status "sold"). That unit is the
thing a return later acts on, and the thing the LifeLedger tracks across lives —
so order history is the source of truth for what can be returned.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.ledger_client import get_ledger_client
from app.models import entities as m
from app.schemas.common import Geo
from app.schemas.orders import CheckoutItem, Order, OrderItem
from app.services.resale import order_item_window


def _cart_as_items(db: Session, user_id) -> list[CheckoutItem]:
    rows = db.execute(
        select(m.CartItem).where(m.CartItem.user_id == user_id)
    ).scalars().all()
    return [
        CheckoutItem(product_id=str(r.product_id), sku=r.sku, size=r.size,
                     variant=r.variant, qty=r.qty)
        for r in rows
    ]


def place_order(
    db: Session,
    *,
    user_id,
    items: list[CheckoutItem] | None = None,
    geo: Geo | None = None,
    clear_cart: bool = True,
) -> m.Order:
    used_cart = items is None
    lines = items if items is not None else _cart_as_items(db, user_id)
    if not lines:
        raise ValueError("nothing to check out (cart is empty)")

    order = m.Order(user_id=user_id, status="placed")
    db.add(order)
    db.flush()

    subtotal = 0.0
    for line in lines:
        product = db.get(m.Product, _as_uuid(line.product_id))
        if product is None:
            continue
        unit = m.ProductUnit(
            product_id=product.id, owner_id=user_id, status="sold",
            serial=f"ORD-{str(order.id)[-6:]}-{str(product.id)[-4:]}",
            size=line.size, geo_lat=geo.lat if geo else None, geo_lng=geo.lng if geo else None,
            transfer_count=0,
        )
        db.add(unit)
        db.flush()
        price = float(product.price)
        subtotal += price * max(1, line.qty)
        db.add(m.OrderItem(
            order_id=order.id, product_id=product.id, unit_id=unit.id,
            sku=line.sku or product.sku, size=line.size, variant=line.variant,
            qty=line.qty, price=price, status="delivered",
            delivered_at=datetime.now(timezone.utc),
        ))
        # First life anchored on the LifeLedger.
        anchor = get_ledger_client().anchor(unit_id=str(unit.id), passport_hash=None)
        db.add(m.LifeLedgerEvent(
            unit_id=unit.id, event_type="PURCHASED", tx_hash=anchor.tx_hash,
        ))

    order.subtotal = round(subtotal, 2)

    if clear_cart and used_cart:
        for r in db.execute(select(m.CartItem).where(m.CartItem.user_id == user_id)).scalars().all():
            db.delete(r)

    db.commit()
    db.refresh(order)
    return order


def _as_uuid(value):
    from app.core.ids import to_uuid
    return to_uuid(value, what="product id")


def order_to_schema(db: Session, order: m.Order) -> Order:
    rows = db.execute(
        select(m.OrderItem).where(m.OrderItem.order_id == order.id)
        .order_by(m.OrderItem.created_at)
    ).scalars().all()

    items: list[OrderItem] = []
    for oi in rows:
        product = db.get(m.Product, oi.product_id)
        window = order_item_window(db, oi, order.user_id)
        items.append(OrderItem(
            id=str(oi.id), product_id=str(oi.product_id),
            unit_id=str(oi.unit_id) if oi.unit_id else None,
            title=product.title if product else None,
            category=product.category if product else None,
            vertical=product.vertical if product else None,
            image_url=product.image_url if product else None,
            sku=oi.sku, size=oi.size, variant=oi.variant, qty=oi.qty,
            price=float(oi.price) if oi.price is not None else None,
            image_category=product.category if product else None,
            delivered_at=window["delivered_at"],
            returnable=window["returnable"],
            resellable=window["resellable"],
            days_to_return_deadline=window["days_to_return_deadline"],
            returned=window["returned"],
            listed=window["listed"],
            return_id=window["return_id"],
            return_state=oi.return_state,
            exchanged_from_id=str(oi.exchanged_from_id) if oi.exchanged_from_id else None,
        ))

    return Order(
        id=str(order.id), user_id=str(order.user_id), status=order.status,
        subtotal=float(order.subtotal) if order.subtotal is not None else None,
        placed_at=order.placed_at, items=items,
    )


def list_orders(db: Session, user_id) -> list[Order]:
    orders = db.execute(
        select(m.Order).where(m.Order.user_id == user_id)
        .order_by(m.Order.placed_at.desc())
    ).scalars().all()
    return [order_to_schema(db, o) for o in orders]
