"""Generate the Second Life seed catalogue assets.

For each catalogue product this:
  1. ATTEMPTS to fetch a real, openly-accessible photo (LoremFlickr → Creative
     Commons Flickr photos matched by keyword; an Unsplash-style real-photo
     source). Re-encodes it to a clean 640x640 JPEG.
  2. FALLS BACK to a clean representative image drawn with Pillow if the fetch
     is blocked/unavailable, and records that in the manifest.

Run once on the host to (re)materialise images + manifest.json:
    python seed_assets/generate_assets.py

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

# (suffix, sku, title, brand, category, vertical, price, sizes, product_url, keyword)
PRODUCTS = [
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

TARGET = (640, 640)


def _try_fetch(keyword: str) -> bytes | None:
    """Fetch a real, keyword-matched CC photo. Returns JPEG bytes or None."""
    try:
        import httpx
        from PIL import Image
    except Exception:
        return None

    urls = [
        f"https://loremflickr.com/{TARGET[0]}/{TARGET[1]}/{keyword.replace(' ', ',')}",
        f"https://picsum.photos/seed/{keyword.replace(' ', '-')}/{TARGET[0]}/{TARGET[1]}",
    ]
    headers = {"User-Agent": "relay-seed-assets/1.0 (+https://relay.dev)"}
    for url in urls:
        try:
            with httpx.Client(follow_redirects=True, timeout=12.0, headers=headers) as c:
                resp = c.get(url)
            if resp.status_code != 200 or not resp.content:
                continue
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            img = img.resize(TARGET)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
        except Exception:
            continue
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

    centered(brand.upper(), 150, font(34), fill=(255, 255, 255))
    # Wrap the title across up to two lines.
    words = title.split()
    line1 = " ".join(words[: (len(words) + 1) // 2])
    line2 = " ".join(words[(len(words) + 1) // 2:])
    centered(line1, 250, font(46))
    if line2:
        centered(line2, 310, font(46))
    centered(category.upper(), 470, font(26), fill=(235, 235, 235))
    centered("RELAY · SECOND LIFE", 520, font(20), fill=(220, 220, 220))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def main() -> None:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    real = generated = 0

    for (suffix, sku, title, brand, category, vertical, price, sizes, url, keyword) in PRODUCTS:
        image_file = f"{category}.jpg"
        data = _try_fetch(keyword)
        if data is not None:
            source = "real:loremflickr/picsum"
            real += 1
        else:
            data = _generate(title, brand, category, vertical)
            source = "generated:pillow"
            generated += 1
        (IMAGES_DIR / image_file).write_bytes(data)

        manifest.append({
            "suffix": suffix,
            "sku": sku,
            "title": title,
            "brand": brand,
            "category": category,
            "vertical": vertical,
            "original_price": price,
            "sizes": sizes,
            "product_url": url,
            "image_file": image_file,
            "image_url": f"/static/products/{image_file}",
            "image_source": source,
            "keyword": keyword,
        })
        print(f"  {category:11s} -> {image_file:18s} [{source}] ({len(data)} bytes)")

    MANIFEST_PATH.write_text(json.dumps({
        "version": 1,
        "note": "Real photos fetched per-keyword where reachable; clean Pillow cards otherwise.",
        "real_images": real,
        "generated_images": generated,
        "products": manifest,
    }, indent=2))
    print(f"\nWrote {len(manifest)} products · real={real} generated={generated}")
    print(f"manifest: {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
