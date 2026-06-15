"""Generate the Second Life seed catalogue assets (≈75 products).

For each catalogue product this materialises a real JPEG + a manifest row
(title, brand, price, category, vertical, sizes, description, image_url). The
image pipeline is layered, best → fallback:

  1. REAL PRODUCT PHOTO from the catalogue source (DummyJSON product images for
     the "extra" depth catalogue; keyword fetch for the curated heroes).
  2. KEYWORD CC PHOTO (LoremFlickr / Picsum) matched to the category.
  3. CLEAN PILLOW CARD drawn locally (always a valid JPEG, fully offline).

Run once on the host to (re)materialise images + manifest.json:
    python seed_assets/generate_assets.py

The catalogue is split into:
  * 14 CURATED heroes (stable 2-hex suffixes a1..ae) — used by the curated demo
    flows in app/services/seed.py. Their metadata must stay stable.
  * ~61 EXTRA products (suffixes x001..) pulled from a real product source to
    give the demo depth (order history, returns, Second Life, Rescue). These
    use deterministic uuid5 ids in the seeder, so suffixes can be free-form.

The resulting JPEGs are real image bytes — usable both as catalogue photos
(served at /static/products/<file>) and as inputs for return/resell grading.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGES_DIR = HERE / "images"
MANIFEST_PATH = HERE / "manifest.json"

TARGET = (640, 640)
USD_TO_INR = 84  # rough demo conversion for DummyJSON USD prices
EXTRA_TARGET = 61  # curated 14 + 61 ≈ 75

# (suffix, sku, title, brand, category, vertical, price, sizes, product_url, keyword)
CURATED = [
    ("a1", "FAS-TS-001", "Cotton Crew Tee", "Allen Solly", "tshirt", "fashion", 899,
     ["S", "M", "L", "XL"], "https://www.amazon.in/dp/B07Q5JZQ7W", "tshirt"),
    ("a2", "FAS-JN-001", "Slim Fit Jeans", "Levi's", "jeans", "fashion", 2499,
     ["30", "32", "34", "36"], "https://www.amazon.in/dp/B07Y5G4F4R", "jeans"),
    ("a3", "FAS-HD-001", "Fleece Hoodie", "H&M", "hoodie", "fashion", 1999,
     ["S", "M", "L", "XL"], "https://www2.hm.com/en_in/productpage.html", "hoodie"),
    ("a4", "ELE-HP-001", "Wireless Headphones", "Sony", "headphones", "electronics", 4999,
     [], "https://www.amazon.in/dp/B09XS7JWHH", "headphones"),
    ("a5", "FAS-JK-001", "Bomber Jacket", "Roadster", "jacket", "fashion", 3499,
     ["S", "M", "L", "XL"], "https://www.myntra.com/jackets", "bomber jacket"),
    ("a6", "FAS-SN-001", "Running Sneakers", "Nike", "sneakers", "fashion", 3999,
     ["7", "8", "9", "10", "11"], "https://www.nike.com/in/w/running-shoes", "sneakers"),
    ("a7", "FAS-DR-001", "Summer Wrap Dress", "Vero Moda", "dress", "fashion", 1799,
     ["XS", "S", "M", "L"], "https://www.myntra.com/dresses", "dress"),
    ("a8", "ELE-SW-001", "Smart Watch Series X", "Samsung", "smartwatch", "electronics", 8999,
     [], "https://www.samsung.com/in/watches/", "smartwatch"),
    ("a9", "FAS-BP-001", "Canvas Daypack", "Wildcraft", "backpack", "fashion", 1599,
     [], "https://www.wildcraft.com/backpacks", "backpack"),
    ("aa", "ELE-SP-001", "Bluetooth Speaker", "JBL", "speaker", "electronics", 2999,
     [], "https://www.jbl.com/bluetooth-speakers/", "bluetooth speaker"),
    ("ab", "ELE-CM-001", "Mirrorless Camera", "Canon", "camera", "electronics", 45999,
     [], "https://www.amazon.in/dp/B08XYZ1234", "camera"),
    ("ac", "FAS-SG-001", "Aviator Sunglasses", "Ray-Ban", "sunglasses", "fashion", 1299,
     [], "https://www.ray-ban.com/india/sunglasses", "sunglasses"),
    ("ad", "ELE-KB-001", "Mechanical Keyboard", "Logitech", "keyboard", "electronics", 5499,
     [], "https://www.logitechg.com/en-in/products/gaming-keyboards", "keyboard"),
    ("ae", "FAS-CT-001", "Wool Overcoat", "Marks & Spencer", "coat", "fashion", 5999,
     ["S", "M", "L", "XL"], "https://www.marksandspencer.in/coats", "wool coat"),
]

# DummyJSON category -> (internal_category, vertical, keyword). Only categories
# that map cleanly to our fashion/electronics demo are pulled; others skipped.
DUMMYJSON_MAP = {
    "smartphones": ("smartphone", "electronics", "smartphone"),
    "laptops": ("laptop", "electronics", "laptop"),
    "tablets": ("tablet", "electronics", "tablet"),
    "mens-watches": ("smartwatch", "electronics", "watch"),
    "womens-watches": ("smartwatch", "electronics", "watch"),
    "mobile-accessories": ("accessory", "electronics", "earbuds"),
    "mens-shirts": ("shirt", "fashion", "shirt"),
    "tops": ("top", "fashion", "top"),
    "womens-dresses": ("dress", "fashion", "dress"),
    "mens-shoes": ("sneakers", "fashion", "shoes"),
    "womens-shoes": ("heels", "fashion", "shoes"),
    "womens-bags": ("backpack", "fashion", "handbag"),
    "womens-jewellery": ("jewellery", "fashion", "jewellery"),
    "sunglasses": ("sunglasses", "fashion", "sunglasses"),
}

_CLOTHING = {"shirt", "top", "dress", "tshirt", "hoodie", "jacket", "coat"}
_SHOES = {"sneakers", "heels", "shoes"}
_JEANS = {"jeans"}


def _sizes_for(category: str) -> list[str]:
    if category in _JEANS:
        return ["30", "32", "34", "36"]
    if category in _SHOES:
        return ["7", "8", "9", "10", "11"]
    if category in _CLOTHING:
        return ["XS", "S", "M", "L", "XL"]
    return []


def _http_client():
    import httpx

    headers = {"User-Agent": "relay-seed-assets/1.0 (+https://relay.dev)"}
    return httpx.Client(follow_redirects=True, timeout=15.0, headers=headers)


def _encode(content: bytes) -> bytes | None:
    """Re-encode arbitrary image bytes to a clean 640x640 JPEG, or None."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(content)).convert("RGB").resize(TARGET)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return None


def _download(url: str, client) -> bytes | None:
    try:
        resp = client.get(url)
        if resp.status_code == 200 and resp.content:
            return _encode(resp.content)
    except Exception:
        return None
    return None


def _try_fetch(keyword: str, client) -> bytes | None:
    """Fetch a real, keyword-matched CC photo. Returns JPEG bytes or None."""
    for url in (
        f"https://loremflickr.com/{TARGET[0]}/{TARGET[1]}/{keyword.replace(' ', ',')}",
        f"https://picsum.photos/seed/{keyword.replace(' ', '-')}/{TARGET[0]}/{TARGET[1]}",
    ):
        data = _download(url, client)
        if data is not None:
            return data
    return None


_PALETTE = {
    "fashion": ((233, 110, 142), (124, 58, 110)),
    "electronics": ((58, 141, 222), (24, 49, 110)),
}


def _generate(title: str, brand: str, category: str, vertical: str) -> bytes:
    """Clean representative product card (always valid JPEG)."""
    from PIL import Image, ImageDraw, ImageFont

    top, bottom = _PALETTE.get(vertical, ((90, 90, 110), (30, 30, 50)))
    w, h = TARGET
    img = Image.new("RGB", (w, h), top)
    px = img.load()
    for y in range(h):
        t = y / h
        r = int(top[0] * (1 - t) + bottom[0] * t)
        g = int(top[1] * (1 - t) + bottom[1] * t)
        b = int(top[2] * (1 - t) + bottom[2] * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([60, 60, w - 60, h - 60], radius=28, outline=(255, 255, 255), width=3)

    def font(size: int):
        for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "arial.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                continue
        try:
            return ImageFont.load_default(size)
        except Exception:
            return ImageFont.load_default()

    def centered(text: str, y: int, fnt, fill=(255, 255, 255)):
        bbox = draw.textbbox((0, 0), text, font=fnt)
        tw = bbox[2] - bbox[0]
        draw.text(((w - tw) / 2, y), text, font=fnt, fill=fill)

    centered(brand.upper()[:22], 150, font(34), fill=(255, 255, 255))
    words = title.split()
    line1 = " ".join(words[: (len(words) + 1) // 2])
    line2 = " ".join(words[(len(words) + 1) // 2:])
    centered(line1[:26], 250, font(46))
    if line2:
        centered(line2[:26], 310, font(46))
    centered(category.upper(), 470, font(26), fill=(235, 235, 235))
    centered("RELAY · SECOND LIFE", 520, font(20), fill=(220, 220, 220))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def _curated_description(title: str, brand: str, category: str, vertical: str) -> str:
    return (
        f"{brand} {title}. A {vertical} {category} from the Relay catalogue — "
        f"AI-graded on return and routed to its best next owner with a verifiable "
        f"Condition Passport."
    )


def _fetch_dummyjson(client, want: int) -> list[dict]:
    """Pull real product metadata (title/description/brand/price/image) and map
    to our fashion/electronics verticals. Returns normalised entries (no image
    bytes yet). Empty list if the source is unreachable."""
    try:
        resp = client.get("https://dummyjson.com/products?limit=0")
        if resp.status_code != 200:
            return []
        raw = resp.json().get("products", [])
    except Exception:
        return []

    out: list[dict] = []
    n = 0
    for p in raw:
        cat = (p.get("category") or "").lower()
        mapped = DUMMYJSON_MAP.get(cat)
        if not mapped:
            continue
        internal_cat, vertical, keyword = mapped
        n += 1
        suffix = f"x{n:03d}"
        title = (p.get("title") or "Product").strip()
        brand = (p.get("brand") or title.split()[0] or "Relay").strip()
        price_usd = float(p.get("price") or 0) or 9.99
        price_inr = max(199, int(round(price_usd * USD_TO_INR)))
        images = p.get("images") or []
        remote = images[0] if images else p.get("thumbnail")
        out.append({
            "suffix": suffix,
            "sku": f"{vertical[:3].upper()}-{internal_cat[:2].upper()}-{n:03d}",
            "title": title[:120],
            "brand": brand[:48],
            "category": internal_cat,
            "vertical": vertical,
            "original_price": price_inr,
            "sizes": _sizes_for(internal_cat),
            "product_url": (p.get("meta") or {}).get("qrCode") or "https://relay.dev",
            "keyword": keyword,
            "description": (p.get("description") or "").strip()[:400],
            "_remote_image": remote,
        })
        if len(out) >= want:
            break
    return out


def main() -> None:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    real = generated = 0

    client = None
    try:
        client = _http_client()
    except Exception:
        client = None

    # ── Curated heroes (stable suffixes; image by keyword) ──
    for (suffix, sku, title, brand, category, vertical, price, sizes, url, keyword) in CURATED:
        image_file = f"{category}.jpg"
        data = _try_fetch(keyword, client) if client else None
        if data is not None:
            source = "real:loremflickr/picsum"
            real += 1
        else:
            data = _generate(title, brand, category, vertical)
            source = "generated:pillow"
            generated += 1
        (IMAGES_DIR / image_file).write_bytes(data)
        manifest.append({
            "suffix": suffix, "sku": sku, "title": title, "brand": brand,
            "category": category, "vertical": vertical, "original_price": price,
            "sizes": sizes, "product_url": url, "image_file": image_file,
            "image_url": f"/static/products/{image_file}", "image_source": source,
            "keyword": keyword,
            "description": _curated_description(title, brand, category, vertical),
        })
        print(f"  curated {category:11s} -> {image_file:20s} [{source}]")

    # ── Extra depth catalogue (real product source → keyword → pillow) ──
    extra = _fetch_dummyjson(client, EXTRA_TARGET) if client else []
    if not extra:
        print("  ! DummyJSON unavailable — generating synthetic extra catalogue")
        extra = _synthetic_extra(EXTRA_TARGET)

    for e in extra:
        category, vertical, keyword = e["category"], e["vertical"], e["keyword"]
        image_file = f"{e['suffix']}_{category}.jpg"
        data = None
        remote = e.get("_remote_image")
        if client and remote:
            data = _download(remote, client)
        if data is not None:
            source = "real:catalogue"
            real += 1
        else:
            data = _try_fetch(keyword, client) if client else None
            if data is not None:
                source = "real:loremflickr/picsum"
                real += 1
            else:
                data = _generate(e["title"], e["brand"], category, vertical)
                source = "generated:pillow"
                generated += 1
        (IMAGES_DIR / image_file).write_bytes(data)
        manifest.append({
            "suffix": e["suffix"], "sku": e["sku"], "title": e["title"], "brand": e["brand"],
            "category": category, "vertical": vertical, "original_price": e["original_price"],
            "sizes": e["sizes"], "product_url": e["product_url"], "image_file": image_file,
            "image_url": f"/static/products/{image_file}", "image_source": source,
            "keyword": keyword, "description": e["description"],
        })
    print(f"  extra catalogue -> {len(extra)} products")

    if client is not None:
        client.close()

    MANIFEST_PATH.write_text(json.dumps({
        "version": 2,
        "note": "Curated heroes + real depth catalogue. Real photos where reachable; "
                "clean Pillow cards otherwise.",
        "real_images": real,
        "generated_images": generated,
        "products": manifest,
    }, indent=2))
    print(f"\nWrote {len(manifest)} products · real={real} generated={generated}")
    print(f"manifest: {MANIFEST_PATH}")


def _synthetic_extra(want: int) -> list[dict]:
    """Offline fallback: deterministic fashion+electronics catalogue so the seed
    still reaches ~75 products with descriptions when no network is available."""
    base = [
        ("smartphone", "electronics", "smartphone", 19999, "Edge", []),
        ("laptop", "electronics", "laptop", 54999, "Nimbus", []),
        ("smartwatch", "electronics", "watch", 7999, "Pulse", []),
        ("headphones", "electronics", "headphones", 3499, "Echo", []),
        ("speaker", "electronics", "speaker", 2799, "Boom", []),
        ("tablet", "electronics", "tablet", 22999, "Slate", []),
        ("shirt", "fashion", "shirt", 1299, "Oxford", ["S", "M", "L", "XL"]),
        ("dress", "fashion", "dress", 1899, "Bloom", ["XS", "S", "M", "L"]),
        ("sneakers", "fashion", "shoes", 3299, "Stride", ["7", "8", "9", "10", "11"]),
        ("backpack", "fashion", "handbag", 1799, "Trail", []),
        ("jeans", "fashion", "jeans", 2199, "Denim Co", ["30", "32", "34", "36"]),
        ("sunglasses", "fashion", "sunglasses", 1499, "Solis", []),
        ("jewellery", "fashion", "jewellery", 2499, "Lumen", []),
        ("hoodie", "fashion", "hoodie", 1699, "Cozy", ["S", "M", "L", "XL"]),
    ]
    out: list[dict] = []
    for i in range(want):
        cat, vertical, keyword, price, brand, sizes = base[i % len(base)]
        n = i + 1
        title = f"{brand} {cat.capitalize()} {n:02d}"
        out.append({
            "suffix": f"x{n:03d}",
            "sku": f"{vertical[:3].upper()}-{cat[:2].upper()}-{n:03d}",
            "title": title, "brand": brand, "category": cat, "vertical": vertical,
            "original_price": price + (n % 7) * 100, "sizes": sizes,
            "product_url": "https://relay.dev", "keyword": keyword,
            "description": f"{brand} {cat} — a {vertical} piece in the Relay catalogue, "
                           f"AI-graded on return with a verifiable Condition Passport.",
            "_remote_image": None,
        })
    return out


if __name__ == "__main__":
    main()
