"""End-to-end integration tests for the return → grade → disposition → listing
flow and Genie instant-match, against the live (seeded) Postgres.

Covers:
- Issue 1: a graded return is routed by disposition and appears in a listing.
- Issue 2: a just-returned rescue item sorts to the TOP of the rescue feed.
- Issue 3: a fresh wish instantly matches a just-graded same-category unit.
- Issue 4: cross-category candidates never surface (jeans wish ≠ jacket).

These hit the real API via TestClient. They require the seeded DB (run
`python -m scripts.seed` first). Each test re-seeds via /demo/reset for isolation.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

BUYER = {"X-User-Id": "00000000-0000-0000-0000-000000000002"}
SELLER = {"X-User-Id": "00000000-0000-0000-0000-000000000001"}
DEMO_GEO = {"lat": 12.9716, "lng": 77.5946}


def _reset() -> dict:
    r = client.post("/demo/reset")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(autouse=True)
def _fresh_db():
    _reset()
    yield


def _png_bytes() -> bytes:
    # 1x1 PNG — enough for the mock/bedrock grader media hash.
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
        b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _first_returnable_order_item() -> dict | None:
    r = client.get("/orders", headers=BUYER)
    assert r.status_code == 200, r.text
    for order in r.json():
        for it in order.get("items", []):
            if it.get("returnable"):
                return it
    return None


# --- Issue 1 + 2: return → grade → disposition → rescue feed (top) ---------

def test_graded_return_appears_in_rescue_feed_on_top():
    item = _first_returnable_order_item()
    if item is None:
        pytest.skip("no returnable order item in seed")

    # 1) Start the return (size/fit → pristine, resell-friendly path).
    r = client.post(
        "/returns",
        json={"order_item_id": item["id"], "reason_code": "fit",
              "pickup_slot": "2030-01-01T10:00:00Z"},
        headers=BUYER,
    )
    assert r.status_code == 201, r.text
    return_id = r.json()["id"]

    # 2) Upload media → grade.
    files = [("files", ("front.png", io.BytesIO(_png_bytes()), "image/png"))]
    r = client.post(f"/returns/{return_id}/media", files=files)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "graded"

    # 3) Disposition → must create a listing for a resalable channel.
    r = client.post(f"/returns/{return_id}/disposition")
    assert r.status_code == 200, r.text
    channel = r.json()["channel"]
    assert channel in {"rescue", "p2p_resale", "exchange", "refurb", "donate", "recycle", "restock"}

    # 4) If routed to a listing channel, it must be visible in the feed (all scopes)
    #    and — being the most-recently-returned — sort to the TOP.
    if channel in {"rescue", "p2p_resale", "refurb"}:
        r = client.get(
            f"/rescue/feed?lat={DEMO_GEO['lat']}&lng={DEMO_GEO['lng']}&radius_km=50&scope=all",
            headers=BUYER,
        )
        assert r.status_code == 200, r.text
        feed = r.json()
        unit_ids = [row["unit_id"] for row in feed]
        assert item["unit_id"] in unit_ids, "graded return missing from rescue feed"
        # Newest-returned-first: our just-returned unit is at the top.
        assert feed[0]["unit_id"] == item["unit_id"], "just-returned item not on top"


# --- Issue 3: a fresh wish instantly matches a just-graded same-category unit -

def test_fresh_wish_instantly_matches_returned_unit():
    item = _first_returnable_order_item()
    if item is None:
        pytest.skip("no returnable order item in seed")
    category = item.get("category") or "hoodie"

    # Return + grade the item so a graded unit of `category` exists.
    r = client.post(
        "/returns",
        json={"order_item_id": item["id"], "reason_code": "changed_mind"},
        headers=BUYER,
    )
    assert r.status_code == 201, r.text
    return_id = r.json()["id"]
    files = [("files", ("front.png", io.BytesIO(_png_bytes()), "image/png"))]
    client.post(f"/returns/{return_id}/media", files=files)

    # A DIFFERENT user wishes for the same category → should match instantly.
    r = client.post(
        "/wishlist",
        json={"category": category, "max_price": 99999, "geo": DEMO_GEO},
        headers=SELLER,
    )
    assert r.status_code == 201, r.text

    r = client.get("/wishlist/matches", headers=SELLER)
    assert r.status_code == 200, r.text
    matches = r.json()
    assert matches, "fresh wish produced no instant matches"
    # Every surfaced match must be the SAME category as the wish (no bleed).
    for mtch in matches:
        assert mtch["category"] == category, (
            f"cross-category match: wished {category}, got {mtch['category']}"
        )


# --- Issue 4: jeans wish must never surface a jacket -----------------------

def test_wish_never_matches_wrong_category():
    # Wish for jeans; assert no jacket/blazer/coat ever comes back.
    r = client.post(
        "/wishlist",
        json={"category": "jeans", "max_price": 99999, "geo": DEMO_GEO},
        headers=BUYER,
    )
    assert r.status_code == 201, r.text

    r = client.get("/wishlist/matches", headers=BUYER)
    assert r.status_code == 200, r.text
    for mtch in r.json():
        cat = (mtch.get("category") or "").lower()
        assert "jacket" not in cat and "coat" not in cat and "blazer" not in cat, (
            f"jeans wish surfaced a wrong-category item: {cat}"
        )
