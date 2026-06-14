"""Demo seed (api-seed). Deterministic IDs so flows + demo are reproducible.

Seeds two personas (demo seller + local buyer), a rich catalog, geo-located
returned units, verifiable Condition Passports anchored on the LifeLedger,
reverse wishes, a mix of public + embargoed rescue listings, return history
for ops signals, and an impressive impact/credit footprint.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.clients.ml_client import get_ml_client
from app.core.deps import DEMO_USER_ID
from app.core.hashing import passport_hash as compute_hash
from app.models import entities as m
from app.schemas.ml import EmbedRequest

# Bangalore-ish demo geo cluster.
BLR = (12.9716, 77.5946)

DEMO_USER = uuid.UUID(DEMO_USER_ID)
BUYER_USER = uuid.UUID("00000000-0000-0000-0000-000000000002")
BUYER_USER_ID = str(BUYER_USER)


def _id(suffix: str) -> uuid.UUID:
    """Stable UUID from a short hex suffix, e.g. 'a3' -> ...0000000000a3."""
    return uuid.UUID(f"00000000-0000-0000-0000-0000000000{suffix}")


# Hero units — stable deep-links for UI + smoke.
U_HOODIE = _id("b1")
U_JEANS = _id("b2")
U_HEADPHONES = _id("b3")
U_JEANS_B = _id("b4")
U_TEE_HIST = _id("b5")

HERO_HOODIE_UNIT_ID = str(U_HOODIE)
HERO_JEANS_UNIT_ID = str(U_JEANS)
HERO_HEADPHONES_UNIT_ID = str(U_HEADPHONES)

# (suffix, sku, title, category, vertical, price, brand)
_PRODUCTS = [
    ("a1", "FAS-TS-001", "Cotton Crew Tee", "tshirt", "fashion", 899, "Relay Basics"),
    ("a2", "FAS-JN-001", "Slim Fit Jeans", "jeans", "fashion", 2499, "Relay Denim"),
    ("a3", "FAS-HD-001", "Fleece Hoodie", "hoodie", "fashion", 1999, "Relay Basics"),
    ("a4", "ELE-HP-001", "Wireless Headphones", "headphones", "electronics", 4999, "Relay Audio"),
    ("a5", "FAS-JK-001", "Bomber Jacket", "jacket", "fashion", 3499, "Relay Outerwear"),
    ("a6", "FAS-SN-001", "Running Sneakers", "sneakers", "fashion", 3999, "Relay Move"),
    ("a7", "FAS-DR-001", "Summer Wrap Dress", "dress", "fashion", 1799, "Relay Femme"),
    ("a8", "ELE-SW-001", "Smart Watch Series X", "smartwatch", "electronics", 8999, "Relay Tech"),
    ("a9", "FAS-BP-001", "Canvas Daypack", "backpack", "fashion", 1599, "Relay Carry"),
    ("aa", "ELE-SP-001", "Bluetooth Speaker", "speaker", "electronics", 2999, "Relay Audio"),
    ("ab", "ELE-CM-001", "Mirrorless Camera", "camera", "electronics", 45999, "Relay Optics"),
    ("ac", "FAS-SG-001", "Aviator Sunglasses", "sunglasses", "fashion", 1299, "Relay Optics"),
    ("ad", "ELE-KB-001", "Mechanical Keyboard", "keyboard", "electronics", 5499, "Relay Tech"),
    ("ae", "FAS-CT-001", "Wool Overcoat", "coat", "fashion", 5999, "Relay Outerwear"),
]

# (suffix, product_suffix, serial, emb_size, status, owner, lat_off, lng_off, transfer, grade, grade_num)
_UNITS = [
    ("b1", "a3", "HD-0001", "M", "returned", "seller", 0.004, 0.005, 1, "A", 0.92),
    ("b2", "a2", "JN-0001", "32", "returned", "buyer", 0.012, 0.010, 0, "B+", 0.80),
    ("b3", "a4", "HP-0001", None, "returned", "seller", -0.030, 0.010, 2, "A", 0.90),
    ("b4", "a2", "JN-0002", "34", "returned", "seller", 0.008, -0.006, 0, "B", 0.72),
    ("b5", "a1", "TS-0001", "M", "recycled", "buyer", 0.000, 0.000, 4, "C", 0.50),
    ("c1", "a5", "JK-0001", "L", "returned", "seller", 0.020, 0.015, 1, "A", 0.88),
    ("c2", "a6", "SN-0001", "9", "returned", "buyer", 0.030, -0.020, 0, "A+", 0.95),
    ("c3", "a7", "DR-0001", "S", "returned", "seller", -0.040, 0.030, 0, "B+", 0.82),
    ("c4", "a8", "SW-0001", None, "returned", "seller", 0.050, 0.040, 1, "A", 0.90),
    ("c5", "a9", "BP-0001", None, "returned", "buyer", -0.020, -0.030, 0, "B", 0.70),
    ("c6", "aa", "SP-0001", None, "returned", "seller", 0.060, -0.050, 2, "B+", 0.78),
    ("c7", "ab", "CM-0001", None, "returned", "seller", 0.070, 0.060, 3, "A", 0.92),
    ("c8", "ac", "SG-0001", None, "returned", "buyer", 0.015, 0.020, 0, "A", 0.90),
    ("c9", "ad", "KB-0001", None, "returned", "seller", -0.050, -0.040, 1, "B+", 0.80),
    ("ca", "ae", "CT-0001", "L", "returned", "seller", 0.090, 0.080, 0, "B", 0.74),
]

# (unit_suffix, base_discount, ttl_hours, age_minutes) — age < 10 ⇒ embargoed (early-access only)
_RESCUES = [
    ("b1", 0.15, 8, 45),
    ("c1", 0.18, 6, 90),
    ("c2", 0.12, 3, 35),
    ("c3", 0.20, 5, 120),
    ("c5", 0.16, 10, 75),
    ("c6", 0.14, 4, 200),
    ("c8", 0.22, 2, 150),
    ("ca", 0.17, 9, 60),
    ("b4", 0.18, 6, 5),   # embargoed
    ("c4", 0.15, 12, 2),  # embargoed
    ("c9", 0.17, 7, 1),   # embargoed
]

# Buyer wishes — each maps to a returned unit category for strong cosine matches.
# (owner, category, emb_size, max_price, days_valid, wish_score)
_WISHES = [
    ("buyer", "hoodie", "M", 2200, 14, 0.82),
    ("buyer", "jeans", "32", 2200, 21, 0.78),
    ("buyer", "sneakers", "9", 4200, 18, 0.86),
    ("buyer", "jacket", "L", 3800, 30, 0.81),
    ("buyer", "headphones", None, 5200, 20, 0.74),
    ("buyer", "backpack", None, 1800, 25, 0.69),
    ("buyer", "dress", "S", 2000, 14, 0.77),
    ("seller", "jeans", "32", 2200, 7, 0.55),
    ("seller", "tshirt", "M", 800, 14, 0.35),
]

# Return history per unit -> drives rescue "reason" + ops high-return signals.
# (unit_suffix, [reason_codes])
_RETURN_HISTORY = [
    ("b5", ["not_as_described", "not_as_described", "too_small", "fit"]),
    ("b2", ["too_small", "fit"]),
    ("c1", ["not_as_described", "defective"]),
    ("b1", ["fit"]),
    ("b4", ["too_large"]),
    ("c2", ["too_small"]),
    ("c3", ["fit"]),
    ("c5", ["defective"]),
    ("c6", ["not_as_described"]),
    ("c8", ["fit"]),
    ("c9", ["too_small"]),
    ("ca", ["too_large"]),
    ("b3", ["defective"]),
    ("c4", ["not_as_described"]),
]

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
    now = datetime.now(timezone.utc)
    owner = {"seller": DEMO_USER, "buyer": BUYER_USER}

    db.add_all([
        m.User(id=DEMO_USER, email="demo@relay.dev", name="Demo Seller", return_rate=0.22,
               fit_profile={"tops": "M", "bottoms": "32"}, rescue_eligible=True),
        m.User(id=BUYER_USER, email="buyer@relay.dev", name="Local Buyer", return_rate=0.05,
               fit_profile={"tops": "M"}, rescue_eligible=True),
    ])
    db.flush()

    products: dict[str, m.Product] = {}
    for suffix, sku, title, category, vertical, price, brand in _PRODUCTS:
        p = m.Product(id=_id(suffix), sku=sku, title=title, category=category,
                      vertical=vertical, price=price, product_metadata={"brand": brand})
        products[suffix] = p
    db.add_all(products.values())
    db.flush()

    def emb(category: str, vertical: str, size: str | None) -> list[float]:
        return ml.embed(EmbedRequest(category=category, vertical=vertical, size=size)).vector

    units: dict[str, m.ProductUnit] = {}
    unit_meta: dict[str, tuple] = {}
    for (usuf, psuf, serial, size, status, who, dlat, dlng, transfer, grade, gnum) in _UNITS:
        prod = products[psuf]
        u = m.ProductUnit(
            id=_id(usuf), product_id=prod.id, serial=serial, status=status,
            owner_id=owner[who], geo_lat=BLR[0] + dlat, geo_lng=BLR[1] + dlng,
            transfer_count=transfer,
            embedding=emb(prod.category, prod.vertical, size),
        )
        units[usuf] = u
        unit_meta[usuf] = (prod, grade, gnum)
    db.add_all(units.values())
    db.flush()

    # Return events: one per unit (drives rescue "reason") + extras for ops signals.
    for usuf, reasons in _RETURN_HISTORY:
        unit = units[usuf]
        for i, reason in enumerate(reasons):
            db.add(m.ReturnEvent(
                unit_id=unit.id, user_id=unit.owner_id, reason_code=reason,
                status="completed", created_at=now - timedelta(days=12 - i, hours=usuf.__hash__() % 12),
            ))

    # Verifiable Condition Passports + anchored LifeLedger (the trust spine).
    # Graded units get a passport hash that re-verifies on /lifeledger/{id}/verify.
    passport_count = 0
    for usuf, unit in units.items():
        prod, grade, gnum = unit_meta[usuf]
        if unit.status == "recycled":
            extra_events = [("GRADED", 40), ("RESCUED", 22), ("RECYCLED", 4)]
        else:
            extra_events = []
        graded_at = now - timedelta(days=6, hours=2)
        media_hash = hashlib.sha256(f"{usuf}-media".encode()).hexdigest()
        payload = {
            "schema_version": "1.0.0",
            "unit_id": str(unit.id),
            "return_id": None,
            "grade": grade,
            "grade_numeric": gnum,
            "category": prod.category,
            "vertical": prod.vertical,
            "disposition_hint": "hyperlocal_rescue",
            "defects": ([] if grade in ("A+", "A") else [
                {"type": "scuff", "severity": "minor", "description": "light cosmetic wear"}
            ]),
            "packaging_state": "opened_resealable",
            "confidence": round(0.80 + gnum * 0.18, 3),
            "media_hashes": [media_hash],
            "graded_at": graded_at.isoformat(),
            "model_tier_used": "mock",
            "warranty_months_remaining": 0,
            "repair_events": [],
        }
        digest = compute_hash(payload)
        payload["passport_hash"] = digest
        db.add(m.ConditionPassport(
            unit_id=unit.id, passport=payload, passport_hash=digest, graded_at=graded_at,
        ))
        passport_count += 1
        db.add(m.LifeLedgerEvent(
            unit_id=unit.id, event_type="GRADED", passport_hash=digest,
            tx_hash=f"0xgr{str(unit.id)[-6:]}", created_at=graded_at,
        ))
        for et, days_ago in extra_events[1:]:
            db.add(m.LifeLedgerEvent(
                unit_id=unit.id, event_type=et,
                tx_hash=f"0x{et[:2].lower()}{str(unit.id)[-6:]}",
                created_at=now - timedelta(days=days_ago),
            ))

    # A couple of hero units get a richer multi-event chain.
    db.add(m.LifeLedgerEvent(unit_id=U_HOODIE, event_type="RESCUED",
                             tx_hash=f"0xrs{str(U_HOODIE)[-6:]}", created_at=now - timedelta(days=1)))
    db.add(m.LifeLedgerEvent(unit_id=U_HEADPHONES, event_type="RESCUED",
                             tx_hash=f"0xrs{str(U_HEADPHONES)[-6:]}", created_at=now - timedelta(days=3)))

    wishes = []
    for who, category, size, max_price, days, score in _WISHES:
        wishes.append(m.ReverseWishlist(
            user_id=owner[who], category=category, size=size, max_price=max_price,
            geo_lat=BLR[0] + 0.01, geo_lng=BLR[1] + 0.01,
            expires_at=now + timedelta(days=days), wish_score=score,
            embedding=emb(category, "electronics" if category in ("headphones", "speaker", "smartwatch", "camera", "keyboard") else "fashion", size),
        ))
    db.add_all(wishes)

    # Rescue listings — mix of public (older) and embargoed (fresh, early-access only).
    embargoed = 0
    for usuf, base, ttl_hours, age_min in _RESCUES:
        ttl = ttl_hours * 3600
        created = now - timedelta(minutes=age_min)
        if age_min < 10:
            embargoed += 1
        db.add(m.RescueListing(
            unit_id=units[usuf].id, base_discount_pct=base, current_discount_pct=base,
            ttl_seconds=ttl, expires_at=now + timedelta(seconds=ttl), status="active",
            created_at=created,
        ))

    db.add_all([
        m.WarrantyRecord(unit_id=U_HEADPHONES, months_remaining=18, repair_events=[]),
        m.WarrantyRecord(unit_id=units["c4"].id, months_remaining=22, repair_events=[]),
        m.WarrantyRecord(unit_id=units["c7"].id, months_remaining=11,
                         repair_events=[{"at": (now - timedelta(days=40)).isoformat(), "note": "sensor recalibrated"}]),
    ])

    # Per-user impact wallets — both personas read as "alive".
    seller_events = [
        ("rescue", 2.4, 30), ("exchange", 1.8, 14), ("refurbish", 3.1, 9),
        ("p2p", 2.0, 21), ("donate", 1.2, 5), ("rescue", 2.6, 3),
        ("rescue", 3.0, 1), ("recycle", 0.9, 18),
    ]
    buyer_events = [
        ("rescue", 2.4, 12), ("rescue", 2.1, 6), ("exchange", 1.6, 20),
        ("p2p", 2.2, 28), ("rescue", 2.8, 2), ("donate", 1.0, 9),
    ]
    for ch, co2, days_ago in seller_events:
        db.add(m.ImpactEvent(user_id=DEMO_USER, channel=ch, co2_saved_kg=co2,
                             created_at=now - timedelta(days=days_ago)))
    for ch, co2, days_ago in buyer_events:
        db.add(m.ImpactEvent(user_id=BUYER_USER, channel=ch, co2_saved_kg=co2,
                             created_at=now - timedelta(days=days_ago)))

    # Network-wide impact backfill (user_id=None) — powers the Ops control-room totals.
    bulk = []
    for i in range(360):
        co2 = 2.0 + ((i * 37) % 28) / 10.0  # 2.0 .. 4.7, deterministic
        channel = "rescue" if i % 4 != 0 else ("refurbish" if i % 8 == 0 else "exchange")
        bulk.append(m.ImpactEvent(user_id=None, channel=channel, co2_saved_kg=co2,
                                  created_at=now - timedelta(hours=i * 2)))
    db.add_all(bulk)

    # Green credits — both personas above the early-access threshold (Pillar 5).
    db.add_all([
        m.GreenCreditLedger(user_id=DEMO_USER, amount=235, reason="seed: prior rescues (early-access tier)",
                            unlock_at=now - timedelta(days=1)),
        m.GreenCreditLedger(user_id=DEMO_USER, amount=45, reason="seed: locked from recent rescue",
                            unlock_at=now + timedelta(days=10)),
        m.GreenCreditLedger(user_id=BUYER_USER, amount=100, reason="seed: rescue rewards (early-access tier)",
                            unlock_at=now - timedelta(days=2)),
        m.GreenCreditLedger(user_id=BUYER_USER, amount=20, reason="seed: locked from recent rescue",
                            unlock_at=now + timedelta(days=7)),
    ])

    # Bracketing demo: 3 sizes of the same tee in the seller's cart.
    for size in ("S", "M", "L"):
        db.add(m.CartItem(user_id=DEMO_USER, product_id=products["a1"].id,
                          sku="FAS-TS-001", size=size, qty=1))

    db.commit()
    return {
        "users": 2,
        "products": len(_PRODUCTS),
        "units": len(_UNITS),
        "passports": passport_count,
        "wishes": len(_WISHES),
        "rescue_listings": len(_RESCUES),
        "embargoed_listings": embargoed,
        "return_events": sum(len(r) for _, r in _RETURN_HISTORY),
        "impact_events": len(seller_events) + len(buyer_events) + len(bulk),
        "cart_items": 3,
        "hero_hoodie_unit": HERO_HOODIE_UNIT_ID,
    }
