"""Layer-1 (Amazon) checkout + order history (api-orders).

A checkout turns cart/explicit lines into an Order with OrderItems, each bound to
a freshly-minted ProductUnit owned by the buyer (status "sold"). That unit is the
thing a return later acts on, and the thing the LifeLedger tracks across lives —
so order history is the source of truth for what can be returned.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.ledger_client import get_ledger_client
from app.core.carbon import credits_for_co2, net_co2_saved
from app.models import entities as m
from app.schemas.common import Geo
from app.schemas.orders import CheckoutItem, Order, OrderItem, RelayCheckoutItem
from app.services.resale import order_item_window

# Order.status sentinel marking a Second-Life / Rescue (resale) checkout, so the
# read side can split Relay orders from Amazon Layer-1 orders without a new column.
RELAY_ORDER_STATUS = "relay_placed"
CREDIT_UNLOCK_DAYS = 14


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
        source="relay" if order.status == RELAY_ORDER_STATUS else "amazon",
        subtotal=float(order.subtotal) if order.subtotal is not None else None,
        placed_at=order.placed_at, items=items,
    )


def list_orders(db: Session, user_id) -> list[Order]:
    orders = db.execute(
        select(m.Order).where(m.Order.user_id == user_id)
        .order_by(m.Order.placed_at.desc())
    ).scalars().all()
    return [order_to_schema(db, o) for o in orders]


# ── Relay (Second-Life + Rescue) cart checkout ───────────────────────────────
def _latest_passport_hash(db: Session, unit_id) -> str | None:
    row = db.execute(
        select(m.ConditionPassport.passport_hash)
        .where(m.ConditionPassport.unit_id == unit_id)
        .order_by(m.ConditionPassport.graded_at.desc())
    ).first()
    return row[0] if row else None


def _award_resale_impact(db: Session, user_id, unit_id, channel: str) -> None:
    """Impact + green credits for a resale acquisition (mirrors resale.buy_listing)."""
    co2 = net_co2_saved(channel)
    db.add(m.ImpactEvent(user_id=user_id, unit_id=unit_id, channel=channel, co2_saved_kg=co2))
    credits = credits_for_co2(co2)
    if credits > 0:
        db.add(m.GreenCreditLedger(
            user_id=user_id, amount=credits, reason=f"relay_checkout:{channel}",
            unlock_at=datetime.now(timezone.utc) + timedelta(days=CREDIT_UNLOCK_DAYS),
        ))


def relay_checkout(
    db: Session,
    *,
    user_id,
    items: list[RelayCheckoutItem],
    geo: Geo | None = None,
) -> m.Order:
    """Mock checkout for the Relay cart (Second-Life listings + Rescue claims).

    Each line runs the real purchase side-effects — ownership transfer, LifeLedger
    anchor, escrow release / claim, impact credits — then we record a single Order
    (``status = relay_placed``) so the buyer can track the buys in their Relay
    order history. Demo flow: self-purchase is allowed (the same demo user can
    list *and* rebuy), and items already gone are skipped idempotently.
    """
    from app.services.rescue import current_discount  # local import: avoid cycle

    if not items:
        raise ValueError("nothing to check out (cart is empty)")

    now = datetime.now(timezone.utc)
    purchased: list[dict] = []

    for line in items:
        listing_uuid = _as_uuid(line.listing_id)

        if line.kind == "second_life":
            listing = db.get(m.ResaleListing, listing_uuid)
            if listing is None or listing.status != "active":
                continue
            unit = db.get(m.ProductUnit, listing.unit_id)
            if unit is None:
                continue
            product = db.get(m.Product, unit.product_id)

            unit.owner_id = user_id
            unit.transfer_count = (unit.transfer_count or 0) + 1
            unit.status = "sold"

            digest = _latest_passport_hash(db, unit.id)
            anchor = get_ledger_client().anchor(unit_id=str(unit.id), passport_hash=digest or "")
            db.add(m.LifeLedgerEvent(
                unit_id=unit.id, event_type="P2P_SOLD", passport_hash=digest, tx_hash=anchor.tx_hash,
            ))

            listing.status = "sold"
            listing.sold_to = user_id
            listing.escrow_status = "released"

            price = (
                float(listing.list_price) if listing.list_price is not None
                else float(product.price) if product is not None else 0.0
            )
            _award_resale_impact(db, user_id, unit.id, "p2p_resale")
            purchased.append({
                "product_id": unit.product_id, "unit_id": unit.id, "price": price, "size": unit.size,
            })

        elif line.kind == "rescue":
            listing = db.get(m.RescueListing, listing_uuid)
            if listing is None or listing.status != "active":
                continue
            unit = db.get(m.ProductUnit, listing.unit_id)
            if unit is None:
                continue
            product = db.get(m.Product, unit.product_id)

            discount = current_discount(listing)
            original = float(product.price) if product is not None else 0.0
            price = round(original * (1 - discount), 2)

            listing.status = "claimed"
            listing.claimed_by = user_id
            listing.current_discount_pct = discount

            unit.owner_id = user_id
            unit.transfer_count = (unit.transfer_count or 0) + 1
            unit.status = "sold"

            digest = _latest_passport_hash(db, unit.id)
            anchor = get_ledger_client().anchor(unit_id=str(unit.id), passport_hash=digest or "")
            db.add(m.LifeLedgerEvent(
                unit_id=unit.id, event_type="RESCUED", passport_hash=digest, tx_hash=anchor.tx_hash,
            ))
            _award_resale_impact(db, user_id, unit.id, "rescue")
            purchased.append({
                "product_id": unit.product_id, "unit_id": unit.id, "price": price, "size": unit.size,
            })

    if not purchased:
        raise ValueError("none of the selected items are still available")

    order = m.Order(user_id=user_id, status=RELAY_ORDER_STATUS)
    db.add(order)
    db.flush()

    subtotal = 0.0
    for p in purchased:
        subtotal += p["price"]
        db.add(m.OrderItem(
            order_id=order.id, product_id=p["product_id"], unit_id=p["unit_id"],
            size=p["size"], qty=1, price=p["price"], status="delivered", delivered_at=now,
        ))
    order.subtotal = round(subtotal, 2)

    db.commit()
    db.refresh(order)
    return order
