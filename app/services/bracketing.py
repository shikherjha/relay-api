"""Bracketing interceptor (api-bracketing).

Active prevention signal: fires when a cart holds >= 3 distinct size/variant of
the SAME product (the classic "buy 3 sizes, return 2" pattern). Strict threshold
per plan.md §7.
"""

from __future__ import annotations

from collections import defaultdict

from app.models import entities as m
from app.schemas.cart import BracketingFlag

BRACKETING_THRESHOLD = 3

_SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL"]


def _suggest(sizes: list[str], fit_profile: dict | None) -> str | None:
    # Prefer the user's known size; else the median of what they bracketed.
    if fit_profile:
        for v in fit_profile.values():
            if v in sizes:
                return v
    ordered = [s for s in _SIZE_ORDER if s in sizes] or sorted(sizes)
    return ordered[len(ordered) // 2] if ordered else None


def detect(items: list[m.CartItem], fit_profile: dict | None = None) -> list[BracketingFlag]:
    by_product: dict[str, set[str]] = defaultdict(set)
    for it in items:
        variant_key = it.size or it.variant
        if variant_key:
            by_product[str(it.product_id)].add(variant_key)

    flags: list[BracketingFlag] = []
    for product_id, variants in by_product.items():
        if len(variants) >= BRACKETING_THRESHOLD:
            sizes = sorted(variants)
            suggested = _suggest(sizes, fit_profile)
            msg = (
                f"You've added {len(variants)} sizes of the same item "
                f"({', '.join(sizes)}). Buying multiple to return most drives "
                f"avoidable returns — we recommend size {suggested}."
                if suggested else
                f"You've added {len(variants)} sizes of the same item ({', '.join(sizes)})."
            )
            flags.append(BracketingFlag(
                flagged=True,
                product_id=product_id,
                distinct_variants=len(variants),
                sizes=sizes,
                suggested_size=suggested,
                message=msg,
            ))
    return flags
