"""End-to-end flow smoke test (step 3). Drives the spine via TestClient against
the live DB. Run inside the container: `python -m scripts.smoke`."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import DEMO_USER_ID
from app.db.session import SessionLocal
from app.main import app
from app.models import entities as m
from app.services.seed import BUYER_USER

client = TestClient(app)
H = {"X-User-Id": DEMO_USER_ID}
BUYER_H = {"X-User-Id": str(BUYER_USER)}


def main() -> None:
    r = client.post("/demo/reset")
    assert r.status_code == 200, r.text
    print("seed:", r.json()["detail"])

    products = client.get("/products").json()
    assert len(products) == 4, products
    fashion = client.get("/products", params={"vertical": "fashion"}).json()
    assert all(p["vertical"] == "fashion" for p in fashion) and len(fashion) == 3

    pdp = client.get(f"/products/{products[0]['id']}").json()
    assert pdp["fit_flags"] is not None, pdp

    cart = client.get("/cart", headers=H).json()
    assert cart["bracketing"] and cart["bracketing"][0]["distinct_variants"] == 3, cart
    print("bracketing:", cart["bracketing"][0]["message"])

    # Grab a returned hoodie unit directly from the DB.
    with SessionLocal() as db:
        unit = db.execute(
            select(m.ProductUnit).join(m.Product, m.Product.id == m.ProductUnit.product_id)
            .where(m.Product.category == "hoodie")
        ).scalars().first()
        unit_id = str(unit.id)

    ret = client.post("/returns", headers=H, json={"unit_id": unit_id, "reason_code": "changed_mind"})
    assert ret.status_code == 201, ret.text
    return_id = ret.json()["id"]

    media = client.post(
        f"/returns/{return_id}/media", headers=H,
        files=[("files", ("unit.jpg", b"fake-image-bytes", "image/jpeg"))],
    )
    assert media.status_code == 202 and media.json()["status"] == "graded", media.text

    passport = client.get(f"/returns/{return_id}/passport").json()
    assert passport["grade"] and passport["passport_hash"], passport
    print("passport:", passport["grade"], passport["passport_hash"][:12])

    engine_mode = "mock" if settings.use_mock_engine else "REAL relay-engine"
    disp = client.post(f"/returns/{return_id}/disposition").json()
    assert disp["channel"] == "rescue", disp
    assert disp["net_co2_saved_kg"] and disp["net_co2_saved_kg"] > 0, disp
    print(f"disposition [{engine_mode}]:", disp["channel"], "co2", disp["net_co2_saved_kg"], "reasons", disp["reasons"])

    impact = client.get("/users/me/impact", headers=H).json()
    # Credits are keep-based: locked for 14 days, so balance is 0 but locked > 0.
    assert impact["total_co2_saved_kg"] > 0 and impact["locked_credits"] > 0, impact
    print("impact:", impact["total_co2_saved_kg"], "kg / locked", impact["locked_credits"],
          "credits (balance", impact["credits_balance"], ")")

    feed = client.get("/rescue/feed", params={"lat": 12.9716, "lng": 77.5946, "radius_km": 10}).json()
    assert len(feed) >= 1 and feed[0]["current_discount_pct"] >= 0.15, feed
    print("rescue feed:", len(feed), "listing(s); nearest", feed[0]["distance_km"],
          "km; decay discount", feed[0]["current_discount_pct"])

    # pgvector cosine matching: buyer's hoodie wish should match the returned hoodie unit.
    matches = client.get("/wishlist/matches", headers=BUYER_H).json()
    assert len(matches) >= 1 and matches[0]["score"] > 0, matches
    print("cosine matches:", len(matches), "top score", matches[0]["score"])

    wish = client.post("/wishlist", headers=H, json={"category": "tshirt", "size": "M"}).json()
    assert wish["wish_score"] is not None, wish

    ops = client.get("/ops/high-return-skus").json()
    assert len(ops) >= 1, ops
    print("ops high-return:", ops[0]["sku"], ops[0]["dominant_reason"], "->", ops[0]["recommendation"])

    verify = client.get(f"/lifeledger/{unit_id}/verify").json()
    assert verify["verified"] is True and verify["tx_hash"], verify
    assert any(e["event_type"] == "GRADED" for e in verify["events"]), verify
    print("lifeledger:", "verified" if verify["verified"] else "TAMPERED",
          "tx", verify["tx_hash"][:14], "events", [e["event_type"] for e in verify["events"]])

    # Pair Rescue: demo (hoodie, wants jeans) ↔ buyer (jeans, wants hoodie).
    pairs = client.get("/rescue/pair-matches", params={"radius_km": 15}).json()
    assert len(pairs) >= 1 and pairs[0]["score"] > 0.6, pairs
    print("pair rescue:", len(pairs), "pair(s); top score", pairs[0]["score"],
          "dist", pairs[0]["distance_km"], "km")

    # Warranty on the electronics unit.
    with SessionLocal() as db:
        hp = db.execute(
            select(m.ProductUnit).join(m.Product, m.Product.id == m.ProductUnit.product_id)
            .where(m.Product.category == "headphones")
        ).scalars().first()
        hp_id = str(hp.id)
    warranty = client.get(f"/units/{hp_id}/warranty").json()
    assert warranty["months_remaining"] == 18, warranty
    print("warranty:", warranty["months_remaining"], "months")

    signals = client.get("/ops/seller-signals").json()
    print("seller-signals:", [(s["sku"], s["recommendation"]) for s in signals])

    print("\nSMOKE_OK")


if __name__ == "__main__":
    main()
