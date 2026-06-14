"""Demo seed (api-seed). Deterministic IDs so the flow + demo are reproducible.

Seeds: users, fashion+electronics catalog, geo-located product units (with
embeddings via relay-ml /embed), reverse wishes, an active rescue listing, and a
≥3-size bracketing cart for the demo user.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.clients.ml_client import get_ml_client
from app.core.deps import DEMO_USER_ID
from app.models import entities as m
from app.schemas.ml import EmbedRequest

# Bangalore-ish demo geo cluster.
BLR = (12.9716, 77.5946)

DEMO_USER = uuid.UUID(DEMO_USER_ID)
BUYER_USER = uuid.UUID("00000000-0000-0000-0000-000000000002")

# Fixed product IDs (referenced by cart/units).
P_TSHIRT = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
P_JEANS = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
P_HOODIE = uuid.UUID("00000000-0000-0000-0000-0000000000a3")
P_HEADPHONES = uuid.UUID("00000000-0000-0000-0000-0000000000a4")

_TABLES = [
    "impact_events", "green_credit_ledger", "warranty_records", "lifeledger_events",
    "cart_items", "p2p_listings", "pair_rescue_matches", "rescue_listings",
    "reverse_wishlist", "condition_passports", "return_events", "product_units",
    "products", "users",
]


def _truncate(db: Session) -> None:
    db.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))


def seed_all(db: Session) -> dict:
    _truncate(db)
    ml = get_ml_client()

    db.add_all([
        m.User(id=DEMO_USER, email="demo@relay.dev", name="Demo Seller", return_rate=0.22,
               fit_profile={"tops": "M", "bottoms": "32"}),
        m.User(id=BUYER_USER, email="buyer@relay.dev", name="Local Buyer", return_rate=0.05,
               fit_profile={"tops": "M"}),
    ])
    db.flush()  # users must exist before FK-dependent rows

    products = [
        m.Product(id=P_TSHIRT, sku="FAS-TS-001", title="Cotton Crew Tee", category="tshirt",
                  vertical="fashion", price=899, product_metadata={"brand": "Relay Basics"}),
        m.Product(id=P_JEANS, sku="FAS-JN-001", title="Slim Fit Jeans", category="jeans",
                  vertical="fashion", price=2499, product_metadata={"brand": "Relay Denim"}),
        m.Product(id=P_HOODIE, sku="FAS-HD-001", title="Fleece Hoodie", category="hoodie",
                  vertical="fashion", price=1999, product_metadata={"brand": "Relay Basics"}),
        m.Product(id=P_HEADPHONES, sku="ELE-HP-001", title="Wireless Headphones", category="headphones",
                  vertical="electronics", price=4999, product_metadata={"brand": "Relay Audio"}),
    ]
    db.add_all(products)
    db.flush()

    def emb(category: str, vertical: str, size: str | None) -> list[float]:
        return ml.embed(EmbedRequest(category=category, vertical=vertical, size=size)).vector

    units = [
        m.ProductUnit(id=uuid.uuid4(), product_id=P_HOODIE, serial="HD-0001", status="returned",
                      owner_id=DEMO_USER, geo_lat=BLR[0], geo_lng=BLR[1],
                      embedding=emb("hoodie", "fashion", "M")),
        # Buyer's returned jeans — satisfies the demo user's jeans wish (Pair Rescue).
        m.ProductUnit(id=uuid.uuid4(), product_id=P_JEANS, serial="JN-0001", status="returned",
                      owner_id=BUYER_USER, geo_lat=BLR[0] + 0.02, geo_lng=BLR[1] + 0.02,
                      embedding=emb("jeans", "fashion", "32")),
        m.ProductUnit(id=uuid.uuid4(), product_id=P_HEADPHONES, serial="HP-0001", status="returned",
                      owner_id=DEMO_USER, geo_lat=BLR[0] - 0.05, geo_lng=BLR[1],
                      embedding=emb("headphones", "electronics", None)),
    ]
    db.add_all(units)
    db.flush()

    # Reverse wishes (local demand). Buyer wants a size-M hoodie nearby.
    wishes = [
        m.ReverseWishlist(user_id=BUYER_USER, category="hoodie", size="M", max_price=2200,
                          geo_lat=BLR[0] + 0.01, geo_lng=BLR[1] + 0.01,
                          expires_at=datetime.now(timezone.utc) + timedelta(days=14),
                          wish_score=0.82, embedding=emb("hoodie", "fashion", "M")),
        m.ReverseWishlist(user_id=DEMO_USER, category="jeans", size="32", max_price=2200,
                          geo_lat=BLR[0], geo_lng=BLR[1],
                          expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                          wish_score=0.4, embedding=emb("jeans", "fashion", "32")),
    ]
    db.add_all(wishes)

    # Active rescue listing on the returned hoodie unit.
    rescue = m.RescueListing(
        unit_id=units[0].id, base_discount_pct=0.15, current_discount_pct=0.15,
        ttl_seconds=8 * 3600, expires_at=datetime.now(timezone.utc) + timedelta(hours=8),
        status="active",
    )
    db.add(rescue)

    # Warranty record on the electronics unit (headphones).
    db.add(m.WarrantyRecord(unit_id=units[2].id, months_remaining=18, repair_events=[]))

    # Bracketing cart: 3 distinct sizes of the same tee for the demo user (fires ≥3).
    for size in ("S", "M", "L"):
        db.add(m.CartItem(user_id=DEMO_USER, product_id=P_TSHIRT, sku="FAS-TS-001", size=size, qty=1))

    db.commit()
    return {
        "users": 2, "products": len(products), "units": len(units),
        "wishes": len(wishes), "rescue_listings": 1, "cart_items": 3, "warranties": 1,
    }
