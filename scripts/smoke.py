"""End-to-end flow smoke test (step 3). Drives the spine via TestClient against
the live DB. Run inside the container: `python -m scripts.smoke`."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import DEMO_USER_ID
from app.db.session import SessionLocal
from app.main import app
from app.models import entities as m
from app.services.seed import BUYER_USER, HERO_HOODIE_UNIT_ID

client = TestClient(app)
H = {"X-User-Id": DEMO_USER_ID}
BUYER_H = {"X-User-Id": str(BUYER_USER)}

# Real seeded product photos double as grading inputs (relay-ml bedrock_only
# rejects non-image bytes; these are valid JPEGs that work in either ML mode).
IMAGES_DIR = Path(__file__).resolve().parents[1] / "seed_assets" / "images"


def _image_for(category: str | None) -> bytes:
    """A real seeded JPEG for the category, falling back to tshirt.jpg. Depth
    categories (laptop, smartphone, …) are stored as ``x###_<cat>.jpg`` so a bare
    ``<cat>.jpg`` won't exist — never return non-image bytes or relay-ml's
    bedrock_only grader rejects them (MIME text/plain) with a 503."""
    path = IMAGES_DIR / f"{category or 'tshirt'}.jpg"
    if not path.exists():
        path = IMAGES_DIR / "tshirt.jpg"
    return path.read_bytes() if path.exists() else b"fake-image-bytes"


def main() -> None:
    r = client.post("/demo/reset")
    assert r.status_code == 200, r.text
    reset = r.json()["detail"]
    print("seed:", reset)

    products = client.get("/products").json()
    # 14 curated heroes + a real "depth" catalogue (~61) for demo richness.
    assert len(products) >= 14, len(products)
    assert all(p.get("image_url") for p in products), "every product needs an image_url"
    fashion = client.get("/products", params={"vertical": "fashion"}).json()
    assert all(p["vertical"] == "fashion" for p in fashion) and len(fashion) >= 9, len(fashion)
    print("catalogue:", len(products), "products · images e.g.", products[0]["image_url"])

    pdp = client.get(f"/products/{products[0]['id']}").json()
    assert pdp["fit_flags"] is not None, pdp

    cart = client.get("/cart", headers=H).json()
    assert cart["bracketing"] and cart["bracketing"][0]["distinct_variants"] == 3, cart
    print("bracketing:", cart["bracketing"][0]["message"])

    # ── Track D · Return Confidence (prevention beyond bracketing, §21.1) ──
    rc = client.get("/cart/return-confidence", headers=H).json()
    assert rc["confidence_band"] in ("high", "medium", "low"), rc
    assert 0.0 <= rc["keep_score"] <= 1.0, rc
    rc_driver_types = {d["type"] for d in rc["drivers"]}
    assert "bracketing" in rc_driver_types, rc  # seller's 3-size tee cart
    assert rc["confidence_band"] in ("low", "medium"), rc
    rc_actions = {iv.get("action") for iv in rc["interventions"]}
    assert "remove_extra_sizes" in rc_actions, rc  # an ACTION, not just a warning
    keep_iv = next(iv for iv in rc["interventions"]
                   if iv["type"] == "size_recommendation" and iv.get("suggested_size"))
    print("return-confidence (cart):", rc["confidence_band"], "keep", rc["keep_score"],
          "· drivers", sorted(rc_driver_types), "· keep size", keep_iv["suggested_size"])

    # Per-line recipients (§21.1 cart v2): the buyer's two hoodie lines are for
    # DIFFERENT people (M for self, L for Priya) → NOT a bracketing return.
    rc_buyer = client.get("/cart/return-confidence", headers=BUYER_H).json()
    buyer_drivers = {d["type"] for d in rc_buyer["drivers"]}
    assert "bracketing" not in buyer_drivers and "duplicate_variant" not in buyer_drivers, rc_buyer
    hoodie_items = [it for it in rc_buyer["items"] if (it["title"] or "").find("Hoodie") >= 0]
    assert len(hoodie_items) == 2, rc_buyer  # one group per recipient, scored apart
    assert {it["for_self"] for it in hoodie_items} == {True, False}, hoodie_items
    # The buyer's electronics line gets a personalized "works with what you own" verdict.
    assert "compatibility_match" in buyer_drivers, rc_buyer
    # The unassigned tee is an "Anyone" gift → scored neutral (no size penalty).
    assert any(it["profile_id"] == "anyone" for it in rc_buyer["items"]), rc_buyer
    print("return-confidence (buyer · per-line):", rc_buyer["confidence_band"],
          "keep", rc_buyer["keep_score"], "· items", len(rc_buyer["items"]))

    # Reassigning the spare hoodie to the SAME person turns it back into a duplicate
    # — proving bracketing is now per-recipient.
    spare_line = next(it for it in hoodie_items if not it["for_self"])["line_ids"][0]
    client.patch(f"/cart/{spare_line}", json={"profile_id": "self"}, headers=BUYER_H)
    rc_same = client.get("/cart/return-confidence", headers=BUYER_H).json()
    assert any(d["type"] == "duplicate_variant" for d in rc_same["drivers"]), rc_same
    client.patch(f"/cart/{spare_line}", json={"profile_id": "priya"}, headers=BUYER_H)  # restore split
    print("per-recipient bracketing: split=clean, same-person=duplicate ✓")

    # PDP path ties prevention to the SAME ops SKU return-health signal (one story).
    ops_health = client.get("/ops/high-return-skus").json()
    fit_reasons = {"too_small", "too_large", "fit", "not_as_described", "defective"}
    health_sku = next((s for s in ops_health
                       if s["dominant_reason"] in fit_reasons and s["recommendation"]), None)
    assert health_sku is not None, ops_health
    health_product = next(p for p in products if p["sku"] == health_sku["sku"])
    pdp_rc = client.get(f"/products/{health_product['id']}/return-confidence",
                        params={"size": "M"}, headers=BUYER_H).json()
    assert "sku_return_health" in {d["type"] for d in pdp_rc["drivers"]}, pdp_rc
    print("return-confidence (PDP · ops tie-in):", health_product["sku"],
          pdp_rc["confidence_band"], "· drivers", sorted({d["type"] for d in pdp_rc["drivers"]}))

    # A confident, in-profile pick reads with a user-fit-confidence driver.
    tee = next(p for p in products if p["sku"] == "FAS-TS-001")
    pdp_hi = client.get(f"/products/{tee['id']}/return-confidence",
                        params={"size": "M"}, headers=BUYER_H).json()
    assert "fit_confidence" in {d["type"] for d in pdp_hi["drivers"]}, pdp_hi
    print("return-confidence (confident pick):", pdp_hi["confidence_band"], "keep", pdp_hi["keep_score"])

    # ── Track D · Fit Profiles ("who are you shopping for?", §21.1 Phase 1) ──
    profiles = client.get("/users/me/fit-profiles", headers=BUYER_H).json()
    assert profiles["active_profile"] == "self", profiles
    assert any(p["is_self"] and p["name"] == "You" for p in profiles["profiles"]), profiles
    partner = next(p for p in profiles["profiles"] if p["relationship"] == "partner")
    assert partner["anchors"].get("tops", {}).get("size") == "S", partner

    hoodie = next(p for p in products if p["sku"] == "FAS-HD-001")
    # SELF (tops M) on the hoodie at size M → recommends M for "You".
    rc_self = client.get(f"/products/{hoodie['id']}/return-confidence",
                         params={"size": "M"}, headers=BUYER_H).json()
    assert rc_self["for_self"] is True and rc_self["profile_name"] == "You", rc_self
    assert rc_self["items"][0]["recommended_size"] == "M", rc_self
    # Same hoodie + same M selection, but shopping for the partner (tops S):
    # personalized → not-for-self, recommends S, surfaces a size-mismatch driver.
    rc_partner = client.get(f"/products/{hoodie['id']}/return-confidence",
                            params={"size": "M", "profile_id": partner["id"]},
                            headers=BUYER_H).json()
    assert rc_partner["for_self"] is False, rc_partner
    assert rc_partner["profile_name"] == partner["name"], rc_partner
    assert rc_partner["items"][0]["recommended_size"] == "S", rc_partner
    assert "size_mismatch" in {d["type"] for d in rc_partner["drivers"]}, rc_partner
    # Buying for someone else suppresses the personal "first time buying" signal.
    assert "new_brand" not in {d["type"] for d in rc_partner["drivers"]}, rc_partner
    print("fit-profiles:", [p["name"] for p in profiles["profiles"]],
          "· self rec", rc_self["items"][0]["recommended_size"],
          "· partner rec", rc_partner["items"][0]["recommended_size"])

    # Setting the active profile persists (it's the default recipient for new PDP
    # adds; the cart itself is now scored per-line, not by one active profile).
    act = client.post("/users/me/fit-profiles/active",
                      json={"profile_id": partner["id"]}, headers=BUYER_H).json()
    assert act["active_profile"] == partner["id"], act
    # Restore self so the rest of the suite stays deterministic.
    client.post("/users/me/fit-profiles/active", json={"profile_id": "self"}, headers=BUYER_H)

    # Electronics "fit-for-purpose" lens (§21.1 Phase 3): no size, but a
    # deterministic compatibility checklist + a high keep score.
    electronics = client.get("/products", params={"vertical": "electronics"}).json()
    assert electronics, "expected an electronics catalogue"
    ele_rc = client.get(
        f"/products/{electronics[0]['id']}/return-confidence", headers=BUYER_H
    ).json()
    compat = next(
        (iv for iv in ele_rc["interventions"] if iv["type"] == "compatibility_check"), None
    )
    assert compat and compat.get("items"), ele_rc
    assert ele_rc["items"][0]["recommended_size"] is None, ele_rc
    print("fit-for-purpose (electronics):", electronics[0]["title"],
          "· checklist", len(compat["items"]), "· keep", ele_rc["keep_score"])

    # Differentiator #1 — personalized "works with what you own" verdict. Buyer's
    # saved setup has phone=ios → headphones read "Set up for your iPhone".
    headphones = next(p for p in electronics if p["sku"] == "ELE-HP-001")
    hp_rc = client.get(f"/products/{headphones['id']}/return-confidence", headers=BUYER_H).json()
    verdicts = [d["label"] for d in hp_rc["drivers"] if d["type"] == "compatibility_match"]
    assert verdicts, hp_rc
    print("electronics verdict:", verdicts[0])

    # Differentiator #2 — "what people actually returned this for" preempt on a
    # flagged electronics SKU (data Amazon's static FAQ never shows).
    flagged_ele = next((p for p in electronics
                        if any(s["sku"] == p["sku"] for s in ops_health)), None)
    if flagged_ele:
        fe_rc = client.get(f"/products/{flagged_ele['id']}/return-confidence", headers=BUYER_H).json()
        fe_item = fe_rc["items"][0]
        assert fe_item["return_reason"], fe_rc
        assert any(iv["type"] == "return_insight" for iv in fe_rc["interventions"]), fe_rc
        print("electronics insight:", flagged_ele["sku"], "→", fe_item["return_reason"],
              f"({int((fe_item['return_reason_share'] or 0) * 100)}%)")

    # Device setup round-trips (electronics §21.1) and lands on the self profile.
    setup_state = client.put("/users/me/setup",
                             json={"setup": {"phone": "ios", "laptop": "usb-c"}},
                             headers=BUYER_H).json()
    assert next(p for p in setup_state["profiles"] if p["is_self"])["setup"].get("phone") == "ios", setup_state

    # Hero hoodie unit — fixed seed ID for reproducible demo deep-links.
    unit_id = HERO_HOODIE_UNIT_ID

    ret = client.post("/returns", headers=H, json={"unit_id": unit_id, "reason_code": "changed_mind"})
    assert ret.status_code == 201, ret.text
    return_id = ret.json()["id"]

    media = client.post(
        f"/returns/{return_id}/media", headers=H,
        files=[("files", ("unit.jpg", _image_for("hoodie"), "image/jpeg"))],
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

    geo = {"lat": 12.9716, "lng": 77.5946, "radius_km": 10}
    feed = client.get("/rescue/feed", params=geo, headers=H).json()
    assert len(feed) >= 5 and feed[0]["current_discount_pct"] >= 0.12, feed
    assert feed[0].get("title"), feed[0]
    print("rescue feed [demo · high-credit]:", len(feed), "listing(s); nearest", feed[0]["distance_km"],
          "km;", feed[0]["title"])

    # Pillar 5: fresh listings are inside their early-access window. Early-access
    # tiers see them flagged; a zero-credit shopper can't see them at all.
    embargoed_ids = {item["id"] for item in feed if item["early_access"]}
    assert embargoed_ids, feed
    print(f"early-access: demo (high-credit) sees {len(embargoed_ids)} embargoed listing(s)")

    nocred = {"X-User-Id": "00000000-0000-0000-0000-0000000000ff"}
    nocred_feed = client.get("/rescue/feed", params=geo, headers=nocred).json()
    assert all(item["id"] not in embargoed_ids for item in nocred_feed), nocred_feed
    assert len(nocred_feed) < len(feed), (len(nocred_feed), len(feed))
    print(f"gating: zero-credit shopper sees {len(nocred_feed)} public listing(s); embargoed hidden")

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
    assert len(signals) >= 1 and signals[0]["recommendation"], signals
    print("seller-signals:", [(s["sku"], s["recommendation"]) for s in signals])

    # ── Layer-1 (Amazon) checkout → order history ──
    co = client.post("/orders/checkout", headers=H, json={})
    assert co.status_code == 201, co.text
    order = co.json()
    assert len(order["items"]) == 3 and order["subtotal"] > 0, order
    print("checkout: order", order["id"][:8], "·", len(order["items"]), "items · subtotal", order["subtotal"])

    history = client.get("/orders", headers=H).json()
    assert len(history) >= 3, len(history)  # 2 seeded + 1 just placed
    print("order history:", len(history), "orders for demo")

    # ── Order-linked + pickup-anchored return → multi-angle grade → Path A list ──
    returnable = next(it for o in history for it in o["items"] if it["returnable"])
    olr = client.post("/returns", headers=H, json={
        "order_item_id": returnable["id"], "reason_code": "changed_mind",
        "pickup_slot": "2026-06-15 10:00-12:00",
    })
    assert olr.status_code == 201, olr.text
    olr_j = olr.json()
    assert olr_j["order_item_id"] == returnable["id"] and olr_j["pickup_at"], olr_j
    assert olr_j["status"] == "picked_up", olr_j
    print("order-linked return:", olr_j["id"][:8], "· status", olr_j["status"], "· pickup", olr_j["pickup_at"][:10])

    # Single image keeps this deterministic in GRADING_MODE=mock. Multi-angle
    # (/grade-images) + video (/grade-video) are Bedrock-only and are verified
    # separately against real images when relay-ml runs in bedrock_only mode.
    media2 = client.post(
        f"/returns/{olr_j['id']}/media", headers=H,
        files=[("files", ("front.jpg", _image_for(returnable.get("category")), "image/jpeg"))],
    )
    assert media2.status_code == 202 and len(media2.json()["media_hashes"]) == 1, media2.text
    print("order-linked grade: graded", len(media2.json()["media_hashes"]), "angle")

    disp2 = client.post(f"/returns/{olr_j['id']}/disposition").json()
    assert disp2["channel"] and disp2["net_co2_saved_kg"] is not None, disp2
    print("order-linked disposition:", disp2["channel"], "co2", disp2["net_co2_saved_kg"])

    # ── Path B: national "Certified Second-Life" relists (shipped, no decay) ──
    nat_feed = client.get("/rescue/feed", params={**geo, "scope": "national"}, headers=H).json()
    assert len(nat_feed) >= 1 and all(x["scope"] == "national" and x["ships"] for x in nat_feed), nat_feed
    print("path B national relists:", len(nat_feed), "· e.g.", nat_feed[0]["title"], "(ships)")

    # ── Tiered early access (credits buy ACCESS) ──
    wallet = client.get("/users/me/impact", headers=H).json()
    assert wallet["tier"] == "gold", wallet
    buyer_wallet = client.get("/users/me/impact", headers=BUYER_H).json()
    assert buyer_wallet["tier"] == "silver", buyer_wallet
    print("tiers: demo", wallet["tier"], "· buyer", buyer_wallet["tier"],
          "· buyer→next", buyer_wallet["next_tier"], "in", buyer_wallet["credits_to_next_tier"])

    # National (Path B) units should surface as shipped Genie matches.
    nat_matches = [mm for mm in client.get("/wishlist/matches", headers=BUYER_H).json()
                   if mm["scope"] == "national"]
    assert len(nat_matches) >= 1, "expected a national (Path B) genie match"
    print("genie national matches:", len(nat_matches), "· top", nat_matches[0]["title"])

    # ══════════════════════════════════════════════════════════════════════
    # Track B — "Second Life" resell / republish
    # ══════════════════════════════════════════════════════════════════════

    # Return-window flags on order history: in-window = returnable, expired = resellable.
    demo_items = [it for o in client.get("/orders", headers=H).json() for it in o["items"]]
    assert any(it["delivered_at"] for it in demo_items), "order items must carry delivered_at"
    returnable_item = next((it for it in demo_items if it["returnable"]), None)
    resellable_item = next((it for it in demo_items if it["resellable"]), None)
    assert returnable_item is not None, "expected an in-window returnable order item"
    assert resellable_item is not None, "expected an out-of-window resellable order item"
    assert resellable_item["days_to_return_deadline"] is not None and resellable_item["days_to_return_deadline"] < 0, resellable_item
    print("return-window: returnable", returnable_item["id"][:8],
          "(", returnable_item["days_to_return_deadline"], "d) · resellable", resellable_item["id"][:8],
          "(", resellable_item["days_to_return_deadline"], "d)")

    # Resell the out-of-window item → a p2p resale listing (grade + price band).
    rcat = resellable_item["category"]
    resell = client.post(
        f"/orders/items/{resellable_item['id']}/resell", headers=H,
        files=[("files", (f"{rcat}.jpg", _image_for(rcat), "image/jpeg"))],
    )
    assert resell.status_code == 201, resell.text
    rl = resell.json()
    assert rl["source"] == "p2p" and rl["status"] == "active", rl
    assert rl["resale_grade"] and rl["passport_id"] and rl["lifeledger_unit_id"], rl
    assert rl["price_range"]["min"] <= rl["list_price"] <= rl["price_range"]["max"] and rl["list_price"] > 0, rl
    resold_id = rl["id"]
    print("resell:", rl["title"], "· grade", rl["resale_grade"], "· list", rl["list_price"],
          "· range", [rl["price_range"]["min"], rl["price_range"]["max"]])

    # The resold line is now "listed" and no longer resellable.
    relisted = next(it for o in client.get("/orders", headers=H).json()
                    for it in o["items"] if it["id"] == resellable_item["id"])
    assert relisted["listed"] and not relisted["resellable"], relisted

    # Second Life catalogue combines p2p (buyer resells) + certified (seller relists).
    catalogue = client.get("/second-life").json()
    sources = {x["source"] for x in catalogue}
    assert "p2p" in sources and "certified" in sources, sources
    assert any(x["id"] == resold_id for x in catalogue), "resold listing should be in the catalogue"
    assert all(x["image_url"] and x["price_range"] for x in catalogue), catalogue[0]
    # S3 media contract: image_url = catalogue image; media_urls = reseller uploads.
    assert all(isinstance(x.get("media_urls"), list) for x in catalogue), catalogue[0]
    assert any(x.get("media_urls") for x in catalogue), "resale listings should carry reseller media_urls"
    fashion_sl = client.get("/second-life", params={"vertical": "fashion"}).json()
    assert all(x["vertical"] == "fashion" for x in fashion_sl), fashion_sl
    img0 = catalogue[0]["image_url"]
    print("second-life:", len(catalogue), "listing(s) · sources", sorted(sources),
          "· img", ("S3" if str(img0).startswith("http") else "static"),
          "· media e.g.", next((x["media_urls"][0] for x in catalogue if x.get("media_urls")), None))

    # Can't buy your own listing.
    own_buy = client.post(f"/second-life/{resold_id}/buy", headers=H)
    assert own_buy.status_code == 400, own_buy.text

    # Buy it as the other persona → stub escrow released + ownership transfer + P2P_SOLD.
    buy = client.post(f"/second-life/{resold_id}/buy", headers=BUYER_H)
    assert buy.status_code == 200, buy.text
    bj = buy.json()
    assert bj["ok"] and bj["escrow_status"] == "released" and bj["tx_hash"], bj
    assert bj["new_owner_id"] == str(BUYER_USER), bj
    # Double-buy is rejected (listing now sold).
    assert client.post(f"/second-life/{resold_id}/buy", headers=BUYER_H).status_code == 409
    sold_verify = client.get(f"/lifeledger/{rl['lifeledger_unit_id']}/verify").json()
    assert any(e["event_type"] == "P2P_SOLD" for e in sold_verify["events"]), sold_verify
    print("buy:", bj["listing_id"][:8], "· escrow", bj["escrow_status"], "· new owner", bj["new_owner_id"][:8],
          "· P2P_SOLD anchored")

    # Seller's refurbished inventory → certified relist (shipped, no decay).
    refurb = client.get("/seller/refurbished", headers=H).json()
    assert len(refurb) >= 1, refurb
    assert all(u["unit_id"] and u["grade"] and u["image_url"] for u in refurb), refurb
    target = refurb[0]
    print("seller refurbished:", len(refurb), "unit(s) · e.g.", target["title"], target["grade"],
          "· last_event", target["last_event"], "· age", target["age_days"], "d")

    # Full seller ORDER HISTORY (broad) — every sold unit across all states, not
    # just the relist-eligible subset. relistable must match /seller/refurbished.
    s_orders = client.get("/seller/orders", headers=H).json()
    assert len(s_orders) >= 3, s_orders
    s_statuses = {s["status"] for s in s_orders}
    assert "delivered" in s_statuses, s_statuses
    assert any(s["relistable"] for s in s_orders), "expected a relistable seller order line"
    assert any(s["listing_id"] for s in s_orders), "expected an already-relisted line with listing_id"
    assert all(s["order_id"] and s["order_item_id"] and s["last_event"] for s in s_orders), s_orders[0]
    sold_ats = [s["sold_at"] for s in s_orders if s["sold_at"]]
    assert sold_ats == sorted(sold_ats, reverse=True), "seller orders must be most-recent first"
    assert {s["unit_id"] for s in s_orders if s["relistable"]} == {u["unit_id"] for u in refurb}, \
        "relistable seller-order lines must match /seller/refurbished exactly"
    print("seller orders:", len(s_orders), "· statuses", sorted(s_statuses),
          "· relistable", sum(1 for s in s_orders if s["relistable"]),
          "· relisted/sold", sum(1 for s in s_orders if s["listing_id"]))

    tcat = target["category"]
    relist = client.post(
        f"/seller/units/{target['unit_id']}/relist", headers=H,
        files=[("files", (f"{tcat}.jpg", _image_for(tcat), "image/jpeg"))],
    )
    assert relist.status_code == 201, relist.text
    cert = relist.json()
    assert cert["source"] == "certified" and cert["ships"] and cert["fulfillment"] == "shipped", cert
    assert cert["list_price"] > 0 and cert["resale_grade"], cert
    # Relisted unit leaves the refurbished pool (already listed).
    refurb_after = client.get("/seller/refurbished", headers=H).json()
    assert all(u["unit_id"] != target["unit_id"] for u in refurb_after), "relisted unit should leave the pool"
    print("relist:", cert["title"], "· certified · ships · list", cert["list_price"])

    # Rescue + wishlist pricing enrichment (list_price + price_range + price_fit).
    rfeed = client.get("/rescue/feed", params=geo, headers=H).json()
    priced = [x for x in rfeed if x.get("list_price") is not None and x.get("price_range")]
    assert priced, "rescue feed listings should carry list_price + price_range"
    print("rescue pricing: e.g.", priced[0]["title"], "list", priced[0]["list_price"],
          "range", [priced[0]["price_range"]["min"], priced[0]["price_range"]["max"]])

    wmatches = client.get("/wishlist/matches", headers=BUYER_H).json()
    assert any("price_fit" in mm for mm in wmatches), wmatches
    assert any(mm.get("list_price") is not None for mm in wmatches), wmatches
    fits = [mm for mm in wmatches if mm.get("price_fit")]
    assert len(fits) >= 1, "expected at least one price-fit wishlist match"
    print("wishlist pricing:", len(wmatches), "match(es) ·", len(fits), "price-fit · e.g.",
          fits[0]["title"], "list", fits[0]["list_price"])

    # ══════════════════════════════════════════════════════════════════════
    # Track C — Return-grading decisions (size pristine · verification ·
    #           size-match gate · wrong_item gate · exchange)
    # ══════════════════════════════════════════════════════════════════════
    vstates = {"match", "mismatch", "unknown"}

    # (D2) Order-vs-item VERIFICATION surfaced on the resold ResaleListing.
    assert rl.get("verification"), f"resale listing should carry a verification block: {rl}"
    rv = rl["verification"]
    assert rv["color_match"] in vstates and rv["item_match"] in vstates, rv
    print("verification (resale):", rv)

    # (D1a) SIZE-RETURN pristine boost (live): a too_small return is graded as a
    # PRISTINE Grade A / "Like New" asset (cosmetic wear cleared, packaging sealed).
    demo_orders = client.get("/orders", headers=H).json()
    size_target = next(it for o in demo_orders for it in o["items"] if it["returnable"])
    sret = client.post("/returns", headers=H, json={
        "order_item_id": size_target["id"], "reason_code": "too_small",
        "pickup_slot": "2026-06-16 10:00-12:00",
    })
    assert sret.status_code == 201, sret.text
    sret_id = sret.json()["id"]
    client.post(f"/returns/{sret_id}/media", headers=H,
                files=[("files", ("unit.jpg", _image_for(size_target.get("category")), "image/jpeg"))])
    spass = client.get(f"/returns/{sret_id}/passport").json()
    assert spass["grade"] in ("A", "A+"), f"size return should be pristine high grade: {spass['grade']}"
    assert not spass["defects"], f"pristine size return should have no defects: {spass}"
    print("size-return pristine (live): grade", spass["grade"], "· defects", len(spass["defects"]),
          "· verification", spass.get("verification"))

    # (D1a) SEED: the size-return pristine unit is live on rescue at only a MINIMAL
    # discount (near original price), graded A — not a deep markdown.
    size_unit = reset["size_return_unit"]
    sfeed = client.get("/rescue/feed", params={**geo, "radius_km": 25}, headers=H).json()
    spristine = next((x for x in sfeed if x["unit_id"] == size_unit), None)
    assert spristine is not None, "seeded size-return unit should be on the rescue feed"
    assert spristine["base_discount_pct"] <= 0.10, f"size return should be minimal-discount: {spristine}"
    assert spristine.get("grade") in ("A", "A+"), f"size return should be high-grade: {spristine}"
    print("size-return pristine (seed):", spristine["title"], "· grade", spristine.get("grade"),
          "· base discount", spristine["base_discount_pct"], "· reason", spristine.get("reason"))

    # (D1b) SIZE-MATCH GATE: the buyer's sneakers wish (size 9, no shoe fit-profile)
    # only matches size-9 units; the seeded size-11 sneaker is filtered out.
    bmatches = client.get("/wishlist/matches", headers=BUYER_H).json()
    sneaker_matches = [mm for mm in bmatches if mm.get("category") == "sneakers"]
    assert sneaker_matches, "expected at least one sneaker match (size 9)"
    assert all(mm.get("size") == "9" for mm in sneaker_matches), \
        f"size gate must filter non-9 sneakers: {sneaker_matches}"
    gate_unit = reset["gate_mismatch_unit"]
    assert all(mm["unit_id"] != gate_unit for mm in bmatches), "size-11 sneaker must be gated out"
    print("size-match gate:", len(sneaker_matches), "sneaker match(es), all size 9; size-11 unit filtered")

    # (D3) WRONG_ITEM — fully gated: flagged return-to-seller. NO grade / passport /
    # GRADED anchor / listing against the ordered unit.
    demo_orders = client.get("/orders", headers=H).json()
    wi_target = next(it for o in demo_orders for it in o["items"] if it["returnable"])
    wi = client.post(f"/orders/items/{wi_target['id']}/return", headers=H,
                     json={"reason": "wrong_item"})
    assert wi.status_code == 201, wi.text
    wi_j = wi.json()
    assert wi_j["status"] == "flagged" and wi_j["reason_code"] == "wrong_item", wi_j
    wi_uid = uuid.UUID(wi_target["unit_id"])
    with SessionLocal() as db:
        graded = db.execute(select(m.LifeLedgerEvent.id)
                            .where(m.LifeLedgerEvent.unit_id == wi_uid)
                            .where(m.LifeLedgerEvent.event_type == "GRADED")).first()
        passp = db.execute(select(m.ConditionPassport.id)
                           .where(m.ConditionPassport.unit_id == wi_uid)).first()
        resc = db.execute(select(m.RescueListing.id)
                          .where(m.RescueListing.unit_id == wi_uid)).first()
        resl = db.execute(select(m.ResaleListing.id)
                          .where(m.ResaleListing.unit_id == wi_uid)).first()
    assert graded is None and passp is None and resc is None and resl is None, \
        "wrong_item must NOT create a passport / GRADED anchor / rescue or resale listing"
    print("wrong_item gated:", wi_j["status"], "· no passport/GRADED/listing created")

    # (D3) Ops seller-signal: the seeded wrong_item SKU surfaces a fulfillment fix.
    signals2 = client.get("/ops/seller-signals").json()
    wi_sig = next((s for s in signals2 if s.get("dominant_reason") == "wrong_item"), None)
    assert wi_sig is not None, f"expected a wrong_item seller signal: {signals2}"
    assert "pick-pack" in wi_sig["recommendation"], wi_sig
    print("ops wrong_item signal:", wi_sig["sku"], "->", wi_sig["recommendation"])

    # (D4) EXCHANGE (live): in-window exchange (NO ML grade) → replacement line +
    # pristine returned unit auto-listed on rescue at ~original price + EXCHANGED.
    demo_orders = client.get("/orders", headers=H).json()
    ex_target = next(it for o in demo_orders for it in o["items"] if it["returnable"])
    ex = client.post(f"/orders/items/{ex_target['id']}/exchange", headers=H,
                     json={"new_size": "L", "pickup_slot": "2026-06-17 10:00-12:00"})
    assert ex.status_code == 200, ex.text
    ex_j = ex.json()
    assert ex_j["exchange_id"] and ex_j["replacement"]["order_item_id"] and ex_j["rescue_listing"]["id"], ex_j
    repl_id = ex_j["replacement"]["order_item_id"]
    all_items = [it for o in client.get("/orders", headers=H).json() for it in o["items"]]
    repl = next((it for it in all_items if it["id"] == repl_id), None)
    assert repl is not None and repl["exchanged_from_id"] == ex_target["id"], repl
    assert repl["size"] == "L", repl
    orig = next(it for it in all_items if it["id"] == ex_target["id"])
    assert orig["return_state"] == "exchanged", orig
    ex_unit = ex_target["unit_id"]
    xfeed = client.get("/rescue/feed", params={**geo, "radius_km": 25}, headers=H).json()
    xlisting = next((x for x in xfeed if x["id"] == ex_j["rescue_listing"]["id"]), None)
    assert xlisting is not None, "exchanged unit should be live on the rescue feed"
    assert xlisting["base_discount_pct"] <= 0.10, f"exchange listing should be ~original price: {xlisting}"
    xverify = client.get(f"/lifeledger/{ex_unit}/verify").json()
    assert any(e["event_type"] == "EXCHANGED" for e in xverify["events"]), xverify
    assert not any(e["event_type"] == "GRADED" for e in xverify["events"]), "exchange must NOT grade"
    print("exchange (live): replacement", repl_id[:8], "size", repl["size"],
          "· rescue", ex_j["rescue_listing"]["id"][:8], "· EXCHANGED anchored")

    # (D4) SEED exchange: returned unit carries an EXCHANGED ledger event.
    seed_ex_unit = reset["exchange_unit"]
    sx_verify = client.get(f"/lifeledger/{seed_ex_unit}/verify").json()
    assert any(e["event_type"] == "EXCHANGED" for e in sx_verify["events"]), sx_verify
    print("exchange (seed): unit", seed_ex_unit[:8], "·", [e["event_type"] for e in sx_verify["events"]])

    print("\nSMOKE_OK")


if __name__ == "__main__":
    main()
