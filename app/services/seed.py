"""Demo seed (api-seed). Deterministic IDs so flows + demo are reproducible.

Seeds two personas (demo seller + local buyer) and a heavily-populated, REAL
catalogue driven by `seed_assets/manifest.json` (each product carries an
`image_url` served from /static/products). On top of that:

* Buyer + seller ORDER HISTORY with mixed `delivered_at` — some lines still
  inside the return window (returnable), some expired (resellable for a Second
  Life), plus order-linked returns.
* Seller-owned REFURBISHED units (sold → returned → refurbished) ready to relist.
* Path A (local pickup-anchored) + Path B (national certified) rescue listings.
* `resale_listings` — a few p2p (buyer resells) + certified (seller relists) for
  the Second Life catalogue.
* Verifiable Condition Passports anchored on the LifeLedger, reverse wishes,
  tiered green credits, and a network-wide impact backfill for the Ops room.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.clients import s3_client
from app.clients.ml_client import MockMLClient, compute_resale_pricing
from app.core.config import settings
from app.core.deps import DEMO_USER_ID
from app.core.hashing import passport_hash as compute_hash
from app.models import entities as m
from app.schemas.ml import EmbedRequest

# Bangalore-ish demo geo cluster.
BLR = (12.9716, 77.5946)

DEMO_USER = uuid.UUID(DEMO_USER_ID)
BUYER_USER = uuid.UUID("00000000-0000-0000-0000-000000000002")
BUYER_USER_ID = str(BUYER_USER)

_SEED_ASSETS = Path(__file__).resolve().parents[2] / "seed_assets"
_MANIFEST_PATH = _SEED_ASSETS / "manifest.json"
_IMAGES_DIR = _SEED_ASSETS / "images"


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

_ELECTRONICS = {"headphones", "smartphone", "laptop", "speaker", "smartwatch", "camera", "keyboard"}


def _load_manifest() -> list[dict]:
    """Real catalogue (title/brand/price/image) from the seed-assets manifest.

    Falls back to a built-in spec if the manifest hasn't been generated, so the
    seed is always runnable (images simply won't resolve until generated)."""
    if _MANIFEST_PATH.exists():
        data = json.loads(_MANIFEST_PATH.read_text())
        return data.get("products", [])
    # Fallback spec (mirrors the manifest products; no image files).
    fallback = [
        ("a1", "FAS-TS-001", "Cotton Crew Tee", "Allen Solly", "tshirt", "fashion", 899),
        ("a2", "FAS-JN-001", "Slim Fit Jeans", "Levi's", "jeans", "fashion", 2499),
        ("a3", "FAS-HD-001", "Fleece Hoodie", "H&M", "hoodie", "fashion", 1999),
        ("a4", "ELE-HP-001", "Wireless Headphones", "Sony", "headphones", "electronics", 4999),
        ("a5", "FAS-JK-001", "Bomber Jacket", "Roadster", "jacket", "fashion", 3499),
        ("a6", "FAS-SN-001", "Running Sneakers", "Nike", "sneakers", "fashion", 3999),
        ("a7", "FAS-DR-001", "Summer Wrap Dress", "Vero Moda", "dress", "fashion", 1799),
        ("a8", "ELE-SW-001", "Smart Watch Series X", "Samsung", "smartwatch", "electronics", 8999),
        ("a9", "FAS-BP-001", "Canvas Daypack", "Wildcraft", "backpack", "fashion", 1599),
        ("aa", "ELE-SP-001", "Bluetooth Speaker", "JBL", "speaker", "electronics", 2999),
        ("ab", "ELE-CM-001", "Mirrorless Camera", "Canon", "camera", "electronics", 45999),
        ("ac", "FAS-SG-001", "Aviator Sunglasses", "Ray-Ban", "sunglasses", "fashion", 1299),
        ("ad", "ELE-KB-001", "Mechanical Keyboard", "Logitech", "keyboard", "electronics", 5499),
        ("ae", "FAS-CT-001", "Wool Overcoat", "Marks & Spencer", "coat", "fashion", 5999),
    ]
    return [
        {"suffix": s, "sku": sku, "title": t, "brand": b, "category": c,
         "vertical": v, "original_price": p, "image_url": None, "product_url": None, "sizes": []}
        for (s, sku, t, b, c, v, p) in fallback
    ]


def _product_image_url(entry: dict) -> str | None:
    """Absolute S3 URL for a product image (idempotent upload), or the local
    /static fallback if S3 isn't configured/reachable."""
    image_file = entry.get("image_file")
    relative = entry.get("image_url")  # e.g. /static/products/tshirt.jpg
    if image_file:
        local = _IMAGES_DIR / image_file
        if local.exists():
            url = s3_client.upload_file_idempotent(
                local, f"{settings.s3_product_prefix}/{image_file}", "image/jpeg",
            )
            if url:
                return url
    return relative


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

# Seller refurbished inventory: sold → returned → refurbished, owner back to the
# seller, graded, NO resale listing yet → surfaces in GET /seller/refurbished.
# (suffix, product_suffix, size, grade, grade_num, age_days, reason)
_SELLER_REFURB = [
    ("e3", "a5", "L", "A", 0.88, 95, "changed_mind"),
    ("e4", "aa", None, "B+", 0.80, 140, "defective"),
    ("e5", "ad", None, "A", 0.92, 70, "not_as_described"),
]

# Pre-listed Second Life catalogue (resale_listings). A few p2p (buyer/seller
# resells) + certified (seller refurb relists) so the catalogue is alive on reset.
# (suffix, product_suffix, source, lister, grade, grade_num, age_days, transfer)
_RESALE_SEED = [
    ("e6", "a4", "p2p", "buyer", "A", 0.90, 120, 0),     # buyer resells headphones
    ("e7", "a2", "p2p", "seller", "B+", 0.80, 210, 0),   # seller resells jeans
    ("e8", "a8", "certified", "seller", "A", 0.90, 300, 1),   # certified smartwatch
    ("e9", "ab", "certified", "seller", "B+", 0.78, 250, 1),  # certified camera
]

_TABLES = [
    "impact_events", "green_credit_ledger", "warranty_records", "lifeledger_events",
    "cart_items", "resale_listings", "p2p_listings", "pair_rescue_matches", "rescue_listings",
    "reverse_wishlist", "condition_passports", "return_events", "order_items",
    "orders", "product_units", "products", "users",
]


def _truncate(db: Session) -> None:
    db.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))


def seed_all(db: Session) -> dict:
    _truncate(db)
    # Seed embeddings use the deterministic local embedder so /demo/reset stays
    # fast and reliable on the LIVE stack (real ML embeds would add ~60 slow
    # Titan round-trips and can time out). Real ML still grades actual
    # return/resell media; only the seed's matching vectors are synthetic.
    ml = MockMLClient()
    now = datetime.now(timezone.utc)
    owner = {"seller": DEMO_USER, "buyer": BUYER_USER}
    manifest = _load_manifest()
    manifest_by_suffix = {e["suffix"]: e for e in manifest}

    db.add_all([
        m.User(id=DEMO_USER, email="demo@relay.dev", name="Demo Seller", return_rate=0.22,
               fit_profile={"tops": "M", "bottoms": "32"}, rescue_eligible=True),
        m.User(id=BUYER_USER, email="buyer@relay.dev", name="Local Buyer", return_rate=0.05,
               fit_profile={"tops": "M"}, rescue_eligible=True),
    ])
    db.flush()

    products: dict[str, m.Product] = {}
    product_image_urls: dict[str, str | None] = {}
    s3_product_uploads = 0
    for entry in manifest:
        suffix = entry["suffix"]
        image_url = _product_image_url(entry)
        product_image_urls[suffix] = image_url
        if image_url and image_url.startswith("http"):
            s3_product_uploads += 1
        p = m.Product(
            id=_id(suffix), sku=entry["sku"], title=entry["title"],
            category=entry["category"], vertical=entry["vertical"],
            price=entry["original_price"], image_url=image_url,
            product_metadata={
                "brand": entry.get("brand"),
                "product_url": entry.get("product_url"),
                "sizes": entry.get("sizes") or [],
            },
        )
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
            id=_id(usuf), product_id=prod.id, serial=serial, status=status, size=size,
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

    def grade_unit(unit, prod, grade, gnum, graded_at, *, hint="rescue"):
        media_hash = hashlib.sha256(f"{unit.id}-media".encode()).hexdigest()
        payload = {
            "schema_version": "1.0.0", "unit_id": str(unit.id), "return_id": None,
            "grade": grade, "grade_numeric": gnum, "category": prod.category,
            "vertical": prod.vertical, "disposition_hint": hint,
            "defects": ([] if grade in ("A+", "A") else [
                {"type": "scuff", "severity": "minor", "description": "light cosmetic wear"}]),
            "packaging_state": "opened", "confidence": round(0.80 + gnum * 0.18, 3),
            "media_hashes": [media_hash], "graded_at": graded_at.isoformat(),
            "model_tier_used": "mock", "warranty_months_remaining": 0, "repair_events": [],
        }
        digest = compute_hash(payload)
        payload["passport_hash"] = digest
        db.add(m.ConditionPassport(unit_id=unit.id, passport=payload,
                                   passport_hash=digest, graded_at=graded_at))
        db.add(m.LifeLedgerEvent(unit_id=unit.id, event_type="GRADED", passport_hash=digest,
                                 tx_hash=f"0xgr{str(unit.id)[-6:]}", created_at=graded_at))
        return digest

    # Verifiable Condition Passports + anchored LifeLedger (the trust spine).
    passport_count = 0
    for usuf, unit in units.items():
        prod, grade, gnum = unit_meta[usuf]
        if unit.status == "recycled":
            extra_events = [("GRADED", 40), ("RESCUED", 22), ("RECYCLED", 4)]
        else:
            extra_events = []
        graded_at = now - timedelta(days=6, hours=2)
        grade_unit(unit, prod, grade, gnum, graded_at)
        passport_count += 1
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
            embedding=emb(category, "electronics" if category in _ELECTRONICS else "fashion", size),
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

    # Green credits drive the tiered access ladder (Pillar 5). Demo seller is a
    # GOLD-tier rescuer (sees every drop from creation); buyer is SILVER.
    db.add_all([
        m.GreenCreditLedger(user_id=DEMO_USER, amount=305, reason="seed: prior rescues (gold tier)",
                            unlock_at=now - timedelta(days=1)),
        m.GreenCreditLedger(user_id=DEMO_USER, amount=60, reason="seed: locked from recent rescue",
                            unlock_at=now + timedelta(days=10)),
        m.GreenCreditLedger(user_id=BUYER_USER, amount=100, reason="seed: rescue rewards (silver tier)",
                            unlock_at=now - timedelta(days=2)),
        m.GreenCreditLedger(user_id=BUYER_USER, amount=20, reason="seed: locked from recent rescue",
                            unlock_at=now + timedelta(days=7)),
    ])

    # Bracketing demo: 3 sizes of the same tee in the seller's cart.
    for size in ("S", "M", "L"):
        db.add(m.CartItem(user_id=DEMO_USER, product_id=products["a1"].id,
                          sku="FAS-TS-001", size=size, qty=1))

    # Hero hoodie: a full first-life → second-life chain for the passport page.
    db.add_all([
        m.LifeLedgerEvent(unit_id=U_HOODIE, event_type="PURCHASED",
                          tx_hash=f"0xpu{str(U_HOODIE)[-6:]}", created_at=now - timedelta(days=14)),
        m.LifeLedgerEvent(unit_id=U_HOODIE, event_type="RETURN_REQUESTED",
                          tx_hash=f"0xrr{str(U_HOODIE)[-6:]}", created_at=now - timedelta(days=7)),
        m.LifeLedgerEvent(unit_id=U_HOODIE, event_type="PICKED_UP",
                          tx_hash=f"0xpk{str(U_HOODIE)[-6:]}", created_at=now - timedelta(days=6, hours=4)),
    ])

    # ── Live in-stock inventory (first-life catalog stock + fresh Genie matches) ──
    # d5 (sneakers size 9) PASSES the buyer's sneakers wish (size 9); d7 (same
    # product, size 11) is a strong cosine candidate that the size-match GATE
    # filters out — proving size equality is enforced when fit confidence is low.
    _INVENTORY = [
        ("d1", "a1", "M"), ("d2", "a2", "32"), ("d3", "a3", "M"),
        ("d4", "a4", None), ("d5", "a6", "9"), ("d6", "a8", None),
        ("d7", "a6", "11"),
    ]
    instock = []
    for usuf, psuf, size in _INVENTORY:
        prod = products[psuf]
        instock.append(m.ProductUnit(
            id=_id(usuf), product_id=prod.id, serial=f"STK-{usuf.upper()}",
            status="in_stock", owner_id=None, size=size,
            geo_lat=BLR[0] + 0.002, geo_lng=BLR[1] + 0.002, transfer_count=0,
            embedding=emb(prod.category, prod.vertical, size),
        ))
    db.add_all(instock)
    db.flush()

    # ── Order history (Layer-1 checkout) for both personas ──
    counts = {"orders": 0, "order_items": 0}

    def make_order(user, days_ago):
        o = m.Order(user_id=user, status="placed", subtotal=0,
                    placed_at=now - timedelta(days=days_ago))
        db.add(o)
        db.flush()
        counts["orders"] += 1
        return o

    def sold_unit(prod, user, days_ago, *, transfer=0, size=None):
        u = m.ProductUnit(product_id=prod.id, owner_id=user, status="sold",
                          serial=f"ORD-{str(prod.id)[-4:]}-{days_ago}", size=size,
                          geo_lat=BLR[0] + 0.006, geo_lng=BLR[1] + 0.004, transfer_count=transfer)
        db.add(u)
        db.flush()
        db.add(m.LifeLedgerEvent(unit_id=u.id, event_type="PURCHASED",
                                 tx_hash=f"0xpu{str(u.id)[-6:]}", created_at=now - timedelta(days=days_ago)))
        return u

    def order_item(order, prod, unit, size, days_ago, status="delivered"):
        oi = m.OrderItem(order_id=order.id, product_id=prod.id, unit_id=unit.id,
                         sku=prod.sku, size=size, qty=1, price=prod.price, status=status,
                         delivered_at=now - timedelta(days=days_ago),
                         created_at=now - timedelta(days=days_ago))
        db.add(oi)
        # Mirror the order-line size onto the physical unit so it carries through
        # the next-owner size-match gate even after it's returned/re-listed.
        if unit is not None and size is not None and getattr(unit, "size", None) is None:
            unit.size = size
        db.flush()
        counts["order_items"] += 1
        order.subtotal = float(order.subtotal or 0) + float(prod.price)
        return oi

    def order_linked_return(order, prod, user, size, reason, grade, gnum, days_ago, pickup_days_ago,
                            base_discount):
        """A purchased line that came back, got graded, and re-listed locally (Path A)."""
        unit = sold_unit(prod, user, days_ago)
        oi = order_item(order, prod, unit, size, days_ago, status="returned")
        unit.status = "returned"
        pickup_at = now - timedelta(days=pickup_days_ago)
        db.add(m.ReturnEvent(
            unit_id=unit.id, order_item_id=oi.id, user_id=user, reason_code=reason,
            status="graded", pickup_slot=f"{(pickup_at).strftime('%Y-%m-%d')} 10:00-12:00",
            pickup_at=pickup_at, created_at=now - timedelta(days=pickup_days_ago + 1),
        ))
        db.flush()
        db.add(m.LifeLedgerEvent(unit_id=unit.id, event_type="RETURN_REQUESTED",
                                 created_at=now - timedelta(days=pickup_days_ago + 1)))
        db.add(m.LifeLedgerEvent(unit_id=unit.id, event_type="PICKED_UP", created_at=pickup_at))
        grade_unit(unit, prod, grade, gnum, pickup_at + timedelta(hours=4))
        unit.embedding = emb(prod.category, prod.vertical, size)
        ttl = 72 * 3600
        db.add(m.RescueListing(
            unit_id=unit.id, base_discount_pct=base_discount, current_discount_pct=base_discount,
            ttl_seconds=ttl, expires_at=pickup_at + timedelta(seconds=ttl),
            status="active", scope="local", fulfillment="local_pickup", created_at=pickup_at,
        ))
        return unit

    # Demo (seller): an OLD order with out-of-window lines (→ resellable), a
    # RECENT order with in-window lines (→ returnable), + one order-linked return.
    do0 = make_order(DEMO_USER, 22)
    order_item(do0, products["a3"], sold_unit(products["a3"], DEMO_USER, 22), "M", 22)   # resellable
    order_item(do0, products["ad"], sold_unit(products["ad"], DEMO_USER, 22), None, 22)  # resellable
    do_recent = make_order(DEMO_USER, 3)
    order_item(do_recent, products["ac"], sold_unit(products["ac"], DEMO_USER, 3), None, 3)  # returnable
    do2 = make_order(DEMO_USER, 12)
    order_linked_return(do2, products["ab"], DEMO_USER, None, "changed_mind", "A", 0.90,
                        days_ago=12, pickup_days_ago=2, base_discount=0.16)

    # Buyer: an OLD order (→ resellable) + a RECENT order (→ returnable) + a return.
    bo0 = make_order(BUYER_USER, 15)
    order_item(bo0, products["a2"], sold_unit(products["a2"], BUYER_USER, 15), "32", 15)  # resellable
    order_item(bo0, products["a6"], sold_unit(products["a6"], BUYER_USER, 15), "9", 15)   # resellable
    bo_recent = make_order(BUYER_USER, 2)
    order_item(bo_recent, products["a9"], sold_unit(products["a9"], BUYER_USER, 2), None, 2)  # returnable
    bo2 = make_order(BUYER_USER, 8)
    order_linked_return(bo2, products["a1"], BUYER_USER, "M", "too_small", "B+", 0.80,
                        days_ago=8, pickup_days_ago=1, base_discount=0.18)

    # ── Return-grading decisions (size pristine boost · wrong_item gate · exchange) ──
    # (a) SIZE-RETURN WINS: a too_small return is a PRISTINE asset → Grade A /
    #     "Like New" + a MINIMAL discount (near-original price), not a markdown.
    size_pristine_order = make_order(BUYER_USER, 9)
    size_return_unit = order_linked_return(
        size_pristine_order, products["a3"], BUYER_USER, "M", "too_small",
        settings.size_return_pristine_grade, settings.size_return_pristine_grade_numeric,
        days_ago=9, pickup_days_ago=2,
        base_discount=settings.size_return_minimal_discount_pct,
    )
    # Sit the minimal-discount listings a bit farther out (still well within the
    # feed radius) so the NEAREST listing — asserted to carry a deeper rescue
    # markdown — is unaffected by these near-original-price pristine units.
    size_return_unit.geo_lat = BLR[0] + 0.03
    size_return_unit.geo_lng = BLR[1] + 0.03
    SIZE_RETURN_UNIT_ID = str(size_return_unit.id)

    # (b) WRONG_ITEM — fully GATED: flagged return-to-seller. NO passport, NO
    #     GRADED anchor, NO listing, unit stays "sold". Two on the same SKU so it
    #     dominates the ops seller-signals (→ pick-pack / SKU-mapping audit).
    wrong_item_order = make_order(BUYER_USER, 5)
    wrong_item_flagged = 0
    for _ in range(2):
        wu = sold_unit(products["a9"], BUYER_USER, 5)
        woi = order_item(wrong_item_order, products["a9"], wu, None, 5, status="delivered")
        woi.return_state = "return_to_seller"
        db.add(m.ReturnEvent(
            unit_id=wu.id, order_item_id=woi.id, user_id=BUYER_USER,
            reason_code="wrong_item", status="flagged",
            created_at=now - timedelta(days=4),
        ))
        wrong_item_flagged += 1

    # (c) EXCHANGE: in-window exchange (NO ML grade). The returned unit is
    #     pristine → live on Path-A rescue at ~original price (minimal discount)
    #     with an EXCHANGED ledger event and a pending pickup; a replacement line
    #     (new size) links back via exchanged_from_id.
    exch_order = make_order(BUYER_USER, 6)
    exch_unit = sold_unit(products["a7"], BUYER_USER, 6, size="S")
    exch_unit.geo_lat = BLR[0] + 0.03  # farther than the nearest deeper-discount listing
    exch_unit.geo_lng = BLR[1] + 0.03
    exch_oi = order_item(exch_order, products["a7"], exch_unit, "S", 6, status="delivered")
    exch_oi.return_state = "exchanged"
    repl_unit = sold_unit(products["a7"], BUYER_USER, 1, size="M")
    repl_oi = order_item(exch_order, products["a7"], repl_unit, "M", 1, status="delivered")
    repl_oi.exchanged_from_id = exch_oi.id
    exch_unit.status = "returned"
    exch_unit.embedding = emb(products["a7"].category, products["a7"].vertical, "S")
    exch_ret = m.ReturnEvent(
        unit_id=exch_unit.id, order_item_id=exch_oi.id, user_id=BUYER_USER,
        reason_code="exchange", status="exchanged",
        pickup_slot=f"{(now + timedelta(days=2)).strftime('%Y-%m-%d')} 10:00-12:00",
        pickup_at=None, created_at=now - timedelta(days=2),
    )
    db.add(exch_ret)
    db.flush()
    db.add(m.LifeLedgerEvent(unit_id=exch_unit.id, event_type="RETURN_REQUESTED",
                             created_at=now - timedelta(days=2)))
    # Deterministic pristine (Grade A) passport — NO ML grade for an exchange.
    exch_payload = {
        "schema_version": "1.0.0", "unit_id": str(exch_unit.id), "return_id": str(exch_ret.id),
        "grade": settings.size_return_pristine_grade,
        "grade_numeric": settings.size_return_pristine_grade_numeric,
        "category": products["a7"].category, "vertical": products["a7"].vertical,
        "disposition_hint": "rescue", "defects": [], "packaging_state": "sealed",
        "confidence": 0.99, "media_hashes": [], "graded_at": (now - timedelta(days=2)).isoformat(),
        "model_tier_used": "exchange-pristine", "warranty_months_remaining": 0, "repair_events": [],
    }
    exch_digest = compute_hash(exch_payload)
    exch_payload["passport_hash"] = exch_digest
    db.add(m.ConditionPassport(unit_id=exch_unit.id, return_id=exch_ret.id,
                               passport=exch_payload, passport_hash=exch_digest,
                               graded_at=now - timedelta(days=2)))
    db.add(m.LifeLedgerEvent(unit_id=exch_unit.id, event_type="EXCHANGED",
                             passport_hash=exch_digest, tx_hash=f"0xex{str(exch_unit.id)[-6:]}",
                             created_at=now - timedelta(days=2)))
    _exch_ttl = 72 * 3600
    db.add(m.RescueListing(
        unit_id=exch_unit.id, base_discount_pct=settings.exchange_minimal_discount_pct,
        current_discount_pct=settings.exchange_minimal_discount_pct,
        ttl_seconds=_exch_ttl, expires_at=now + timedelta(seconds=_exch_ttl),
        status="active", scope="local", fulfillment="local_pickup",
        created_at=now - timedelta(days=2),  # past → public on the rescue feed
    ))
    EXCHANGE_UNIT_ID = str(exch_unit.id)
    exchanges = 1

    # ── Seller refurbished inventory (sold → returned → refurbished, owned by
    #    the seller, NO resale listing) → GET /seller/refurbished + relist. ──
    refurb_order = make_order(BUYER_USER, 60)
    seller_refurb_units = 0
    for usuf, psuf, size, grade, gnum, age_days, reason in _SELLER_REFURB:
        prod = products[psuf]
        u = m.ProductUnit(
            id=_id(usuf), product_id=prod.id, serial=f"RFB-{usuf.upper()}",
            status="refurbished", owner_id=DEMO_USER, size=size,  # back with the seller to relist
            geo_lat=BLR[0] + 0.003, geo_lng=BLR[1] + 0.003, transfer_count=1,
            embedding=emb(prod.category, prod.vertical, size),
        )
        db.add(u)
        db.flush()
        oi = m.OrderItem(order_id=refurb_order.id, product_id=prod.id, unit_id=u.id,
                         sku=prod.sku, size=size, qty=1, price=prod.price, status="returned",
                         delivered_at=now - timedelta(days=age_days),
                         created_at=now - timedelta(days=age_days))
        db.add(oi)
        counts["order_items"] += 1
        refurb_order.subtotal = float(refurb_order.subtotal or 0) + float(prod.price)
        db.add(m.ReturnEvent(unit_id=u.id, order_item_id=oi.id, user_id=BUYER_USER,
                             reason_code=reason, status="graded",
                             created_at=now - timedelta(days=age_days - 5)))
        db.add_all([
            m.LifeLedgerEvent(unit_id=u.id, event_type="PURCHASED",
                              tx_hash=f"0xpu{str(u.id)[-6:]}", created_at=now - timedelta(days=age_days)),
            m.LifeLedgerEvent(unit_id=u.id, event_type="RETURN_REQUESTED",
                              created_at=now - timedelta(days=age_days - 5)),
            m.LifeLedgerEvent(unit_id=u.id, event_type="PICKED_UP",
                              created_at=now - timedelta(days=age_days - 6)),
        ])
        grade_unit(u, prod, grade, gnum, now - timedelta(days=age_days - 7), hint="refurb")
        db.add(m.LifeLedgerEvent(unit_id=u.id, event_type="REFURBISHED",
                                 created_at=now - timedelta(days=age_days - 9)))
        seller_refurb_units += 1
    db.flush()

    # ── Pre-listed Second Life catalogue (resale_listings: p2p + certified) ──
    # Each resale-seed unit also gets a sale-origin order line so it appears in
    # the lister's order history (seller-owned ones surface as "relisted" with a
    # listing_id in GET /seller/orders).
    resale_origin_order = make_order(BUYER_USER, 70)
    resale_p2p = resale_certified = 0
    for usuf, psuf, source, who, grade, gnum, age_days, transfer in _RESALE_SEED:
        prod = products[psuf]
        lister = owner[who]
        u = m.ProductUnit(
            id=_id(usuf), product_id=prod.id, serial=f"SL-{usuf.upper()}",
            status="refurbished" if source == "certified" else "listed",
            owner_id=lister, geo_lat=BLR[0] + 0.004, geo_lng=BLR[1] - 0.004,
            transfer_count=transfer, embedding=emb(prod.category, prod.vertical, None),
        )
        db.add(u)
        db.flush()
        oi = m.OrderItem(
            order_id=resale_origin_order.id, product_id=prod.id, unit_id=u.id,
            sku=prod.sku, qty=1, price=prod.price, status="returned",
            delivered_at=now - timedelta(days=age_days),
            created_at=now - timedelta(days=age_days),
        )
        db.add(oi)
        counts["order_items"] += 1
        resale_origin_order.subtotal = float(resale_origin_order.subtotal or 0) + float(prod.price)
        db.add(m.LifeLedgerEvent(unit_id=u.id, event_type="PURCHASED",
                                 tx_hash=f"0xpu{str(u.id)[-6:]}", created_at=now - timedelta(days=age_days)))
        grade_unit(u, prod, grade, gnum, now - timedelta(days=age_days - 10),
                   hint="refurb" if source == "certified" else "p2p_resale")
        if source == "certified":
            db.add_all([
                m.LifeLedgerEvent(unit_id=u.id, event_type="REFURBISHED",
                                  created_at=now - timedelta(days=age_days - 12)),
                m.LifeLedgerEvent(unit_id=u.id, event_type="RELISTED",
                                  created_at=now - timedelta(days=age_days - 13)),
            ])
            resale_certified += 1
        else:
            resale_p2p += 1
        price_range, list_price = compute_resale_pricing(
            grade_numeric=gnum, original_price=float(prod.price), age_days=age_days,
        )
        # Reseller-uploaded photos: upload the product image as a stand-in
        # "reseller photo" under the resale prefix so media_urls are real S3
        # objects, with a graceful fallback to the catalogue image (so the UI
        # always shows image_url + media_urls even when S3 is unreachable).
        reseller_media: list[str] = []
        img_file = (manifest_by_suffix.get(psuf) or {}).get("image_file")
        if img_file and (_IMAGES_DIR / img_file).exists():
            up = s3_client.upload_file_idempotent(
                _IMAGES_DIR / img_file,
                f"{settings.s3_resale_prefix}/seed/{usuf}/{img_file}", "image/jpeg",
            )
            if up:
                reseller_media.append(up)
        if not reseller_media and product_image_urls.get(psuf):
            reseller_media.append(product_image_urls[psuf])
        db.add(m.ResaleListing(
            unit_id=u.id, lister_id=lister, source=source,
            original_price=float(prod.price), price_min=price_range.min, price_max=price_range.max,
            list_price=list_price, resale_grade=grade, age_days=age_days,
            media_urls=reseller_media,
            pricing_rationale=f"Seed · grade {grade} · ~{max(1, age_days // 30)} months old",
            status="active", escrow_status="none",
            created_at=now - timedelta(days=age_days - 14),
        ))
        db.add(m.LifeLedgerEvent(unit_id=u.id, event_type="P2P_LISTED",
                                 created_at=now - timedelta(days=age_days - 14)))

    # ── Path B: warehouse "Certified Second-Life" national relists (shipped) ──
    _PATH_B = [("e1", "a4", "B+", 0.80), ("e2", "ab", "A", 0.90)]
    national_listings = 0
    for usuf, psuf, grade, gnum in _PATH_B:
        prod = products[psuf]
        u = m.ProductUnit(
            id=_id(usuf), product_id=prod.id, serial=f"REF-{usuf.upper()}",
            status="graded", owner_id=None,
            geo_lat=BLR[0] + 0.5, geo_lng=BLR[1] + 0.5, transfer_count=1,  # far → national only
            embedding=emb(prod.category, prod.vertical, None),
        )
        db.add(u)
        db.flush()
        db.add_all([
            m.LifeLedgerEvent(unit_id=u.id, event_type="PURCHASED",
                              tx_hash=f"0xpu{str(u.id)[-6:]}", created_at=now - timedelta(days=40)),
            m.LifeLedgerEvent(unit_id=u.id, event_type="RETURN_REQUESTED",
                              created_at=now - timedelta(days=20)),
            m.LifeLedgerEvent(unit_id=u.id, event_type="PICKED_UP",
                              created_at=now - timedelta(days=19)),
        ])
        grade_unit(u, prod, grade, gnum, now - timedelta(days=18), hint="refurb")
        db.add_all([
            m.LifeLedgerEvent(unit_id=u.id, event_type="REFURBISHED",
                              created_at=now - timedelta(days=10)),
            m.LifeLedgerEvent(unit_id=u.id, event_type="RELISTED",
                              created_at=now - timedelta(days=9)),
        ])
        db.add(m.RescueListing(
            unit_id=u.id, base_discount_pct=0.30, current_discount_pct=0.30,
            ttl_seconds=None, expires_at=None,  # no time decay for national relist
            status="active", scope="national", fulfillment="shipped",
            created_at=now - timedelta(days=9),
        ))
        national_listings += 1

    db.commit()
    return {
        "users": 2,
        "products": len(manifest),
        "units": (len(_UNITS) + len(_INVENTORY) + counts["order_items"]
                  + seller_refurb_units + len(_RESALE_SEED) + national_listings),
        "passports": passport_count,
        "wishes": len(_WISHES),
        "orders": counts["orders"],
        "order_items": counts["order_items"],
        "rescue_listings_local": len(_RESCUES) + 2,
        "rescue_listings_national": national_listings,
        "embargoed_listings": embargoed,
        "resale_listings_p2p": resale_p2p,
        "resale_listings_certified": resale_certified,
        "seller_refurbished_units": seller_refurb_units,
        "in_stock_units": len(_INVENTORY),
        # Return-grading decisions (size pristine boost · wrong_item gate · exchange).
        "wrong_item_flagged": wrong_item_flagged,
        "exchanges": exchanges,
        "size_return_unit": SIZE_RETURN_UNIT_ID,
        "exchange_unit": EXCHANGE_UNIT_ID,
        "gate_mismatch_unit": str(_id("d7")),
        "return_events": (sum(len(r) for _, r in _RETURN_HISTORY) + 3
                          + seller_refurb_units + wrong_item_flagged + exchanges),
        "impact_events": len(seller_events) + len(buyer_events) + len(bulk),
        "cart_items": 3,
        "hero_hoodie_unit": HERO_HOODIE_UNIT_ID,
        "s3_enabled": s3_client.s3_configured(),
        "s3_strategy": s3_client.active_strategy(),
        "product_images_on_s3": s3_product_uploads,
    }
