"""Manual verification of the bug fixes against the live seeded API.

Run inside the relay-api container:
    docker exec relay-api python scripts/verify_fixes.py
"""

import io
import httpx

BASE = "http://127.0.0.1:8010"
BUYER = {"X-User-Id": "00000000-0000-0000-0000-000000000002"}
SELLER = {"X-User-Id": "00000000-0000-0000-0000-000000000001"}
GEO = {"lat": 12.9716, "lng": 77.5946}

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
    b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)

c = httpx.Client(base_url=BASE, timeout=120.0)


def first_returnable():
    orders = c.get("/orders", headers=BUYER).json()
    for o in orders:
        for it in o.get("items", []):
            if it.get("returnable"):
                return it
    return None


def main():
    print("=" * 60)
    print("ISSUE 1 + 2: return → grade → disposition → feed top")
    print("=" * 60)
    item = first_returnable()
    if not item:
        print("  [SKIP] no returnable order item")
    else:
        print(f"  Returning: {item['title']} ({item.get('category')}) unit={item['unit_id'][:8]}")
        r = c.post("/returns", json={
            "order_item_id": item["id"], "reason_code": "fit",
            "pickup_slot": "2030-01-01T10:00:00Z",
        }, headers=BUYER)
        rid = r.json()["id"]
        print(f"  Return created: {rid[:8]} status={r.json()['status']}")

        r = c.post(f"/returns/{rid}/media",
                   files=[("files", ("front.png", io.BytesIO(PNG), "image/png"))])
        print(f"  Graded: status={r.json().get('status')}")

        r = c.post(f"/returns/{rid}/disposition")
        ch = r.json()["channel"]
        print(f"  Disposition channel: {ch}")

        feed = c.get(f"/rescue/feed?lat={GEO['lat']}&lng={GEO['lng']}&radius_km=50&scope=all",
                     headers=BUYER).json()
        ids = [row["unit_id"] for row in feed]
        in_feed = item["unit_id"] in ids
        on_top = feed and feed[0]["unit_id"] == item["unit_id"]
        print(f"  In feed: {in_feed}  |  On top: {on_top}")
        if feed:
            print(f"  Top item: {feed[0]['title']} returned_at={feed[0].get('returned_at')}")

    print()
    print("=" * 60)
    print("ISSUE 3 + 4: fresh wish matches same category only")
    print("=" * 60)
    for cat in ("jeans", "hoodie", "headphones"):
        r = c.post("/wishlist", json={"category": cat, "max_price": 999999, "geo": GEO},
                   headers=SELLER)
        if r.status_code != 201:
            print(f"  [{cat}] wish failed: {r.status_code}")
            continue
        matches = c.get("/wishlist/matches", headers=SELLER).json()
        cats = sorted({m.get("category") for m in matches})
        bleed = [cat2 for cat2 in cats if cat2 and cat2 != cat]
        print(f"  wish={cat:12s} matches={len(matches):2d} categories={cats}")
        if bleed:
            print(f"    ⚠️  CROSS-CATEGORY BLEED: {bleed}")
        else:
            print(f"    ✓ no cross-category bleed")
        # cleanup: delete the wishes we just made
        for w in c.get("/wishlist", headers=SELLER).json():
            if w["category"] == cat:
                c.delete(f"/wishlist/{w['id']}", headers=SELLER)

    print("\nDONE")


if __name__ == "__main__":
    main()
