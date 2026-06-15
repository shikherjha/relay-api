"""Lightweight product taxonomy for reverse-wishlist matching.

Cosine similarity alone is too coarse for a two-vertical catalogue — a "macbook"
wish sits near "earphones" and a "hoodie" near a "tee" in embedding space. This
module gives a deterministic category-alignment layer (the cheap, reliable half
of the industry retrieve→rerank pattern): canonicalise free text to a category
token, then score how well a candidate's category matches the wish.

It is dependency-free so both the relay-api matcher and the Mock ML client can
use it without import cycles.
"""

from __future__ import annotations

# Canonical category token -> synonyms/keywords that map onto it. Order matters
# only for overlapping substrings (more specific first where it could collide).
_CATEGORY_SYNONYMS: dict[str, tuple[str, ...]] = {
    # ── electronics ──
    "laptop": ("laptop", "macbook", "notebook", "ultrabook", "chromebook"),
    "headphones": ("headphone", "earphone", "earbud", "airpod", "headset", "buds"),
    "smartphone": ("smartphone", "iphone", "android", "pixel", "galaxy", "oneplus",
                   "oppo", "vivo", "redmi", "phone"),
    "speaker": ("speaker", "soundbar", "boombox"),
    "smartwatch": ("smartwatch", "wearable", "fitness band", "watch"),
    "camera": ("camera", "dslr", "mirrorless", "gopro", "lens"),
    "keyboard": ("keyboard", "keypad"),
    "mouse": ("mouse", "trackpad"),
    "tablet": ("tablet", "ipad", "kindle"),
    "monitor": ("monitor", "display"),
    # ── fashion ──
    "tshirt": ("tshirt", "t-shirt", "tee", "crew tee"),
    "hoodie": ("hoodie", "sweatshirt", "pullover", "fleece"),
    "jeans": ("jeans", "denim"),
    "jacket": ("jacket", "blazer", "coat", "parka"),
    "sneakers": ("sneakers", "trainers", "shoe", "footwear", "running"),
    "dress": ("dress", "gown", "frock"),
    "backpack": ("backpack", "rucksack", "bag"),
    "sunglasses": ("sunglasses", "shades", "eyewear", "glasses"),
    "skirt": ("skirt",),
    "shorts": ("shorts",),
}

_ELECTRONICS = {
    "laptop", "headphones", "smartphone", "speaker", "smartwatch",
    "camera", "keyboard", "mouse", "tablet", "monitor",
}


def canonical_category(text: str | None) -> str | None:
    """Map free text (a wish, a product category, or a title) to a canonical
    category token, or None if nothing recognisable matches."""
    if not text:
        return None
    t = text.lower()
    t_norm = t.replace("_", " ").replace("-", " ")
    for canon, words in _CATEGORY_SYNONYMS.items():
        for w in words:
            if w in t or w in t_norm:
                return canon
    return None


def vertical_for(canon: str | None) -> str | None:
    if canon is None:
        return None
    return "electronics" if canon in _ELECTRONICS else "fashion"


def classify_vertical(text: str | None) -> str | None:
    """Vertical for a free-text wish; None only for empty/unknown input."""
    canon = canonical_category(text)
    if canon is not None:
        return vertical_for(canon)
    return None


def category_relevance(
    wish_text: str | None,
    candidate_category: str | None,
    candidate_title: str | None = None,
) -> float:
    """Deterministic 0..1 relevance of a candidate to a wish, by category.

    1.0  exact canonical-category match (macbook ↔ laptop)
    0.25 same vertical, different category (laptop ↔ headphones)
    0.0  different vertical (laptop ↔ tee) or clearly unrelated
    0.5  wish can't be classified → stay neutral (don't over-filter)
    0.3  wish known but candidate unknown → mild
    """
    wc = canonical_category(wish_text)
    cc = canonical_category(candidate_category) or canonical_category(candidate_title)
    if wc is None:
        return 0.5
    if cc is None:
        return 0.3
    if wc == cc:
        return 1.0
    return 0.25 if vertical_for(wc) == vertical_for(cc) else 0.0
