"""Category-gate tests for next-owner / Genie matching.

Covers the fix for cross-category bleed: a "jeans" wish must never surface a
jacket, a "macbook" wish must never surface earphones, regardless of how
generous the LLM reranker is. The deterministic taxonomy layer is the hard
gate; these tests pin its behaviour and the MATCH_RELEVANCE_FLOOR contract.
"""

from app.core.taxonomy import (
    canonical_category,
    category_relevance,
    classify_vertical,
    vertical_for,
)
from app.services.matching import MATCH_RELEVANCE_FLOOR


# --- canonical_category ---------------------------------------------------

def test_canonical_exact_tokens():
    assert canonical_category("jeans") == "jeans"
    assert canonical_category("laptop") == "laptop"
    assert canonical_category("headphones") == "headphones"


def test_canonical_synonyms():
    assert canonical_category("macbook") == "laptop"
    assert canonical_category("earbuds") == "headphones"
    assert canonical_category("denim") == "jeans"
    assert canonical_category("blazer") == "jacket"
    assert canonical_category("trainers") == "sneakers"


def test_canonical_unknown_is_none():
    assert canonical_category("flux capacitor") is None
    assert canonical_category("") is None
    assert canonical_category(None) is None


def test_vertical_classification():
    assert vertical_for("laptop") == "electronics"
    assert vertical_for("jeans") == "fashion"
    assert classify_vertical("macbook") == "electronics"
    assert classify_vertical("denim") == "fashion"
    assert classify_vertical("unknownthing") is None


# --- category_relevance ---------------------------------------------------

def test_exact_category_is_full_relevance():
    assert category_relevance("jeans", "jeans", "Blue Denim") == 1.0
    assert category_relevance("macbook", "laptop", "MacBook Air") == 1.0


def test_same_vertical_different_category_is_below_floor():
    # The core bug: jeans wish must NOT match a jacket. Same vertical (fashion)
    # but different category → 0.25, which is below the match floor → vetoed.
    rel = category_relevance("jeans", "jacket", "Wool Blazer")
    assert rel == 0.25
    assert rel < MATCH_RELEVANCE_FLOOR  # gets filtered out

    rel_e = category_relevance("macbook", "headphones", "Wireless Earbuds")
    assert rel_e == 0.25
    assert rel_e < MATCH_RELEVANCE_FLOOR


def test_cross_vertical_is_zero():
    # A laptop wish vs a t-shirt — different verticals → hard 0.0.
    assert category_relevance("laptop", "tshirt", "Cotton Tee") == 0.0
    assert category_relevance("jeans", "smartphone", "Galaxy S24") == 0.0


def test_unclassifiable_wish_stays_neutral():
    # If we can't classify the wish, don't over-filter (neutral 0.5).
    rel = category_relevance("flux capacitor", "jeans", "Blue Denim")
    assert rel == 0.5
    assert rel >= MATCH_RELEVANCE_FLOOR  # neutral wishes are not vetoed


def test_known_wish_unknown_candidate_is_mild():
    rel = category_relevance("jeans", "flux", "Mystery Item")
    assert rel == 0.3
    assert rel < MATCH_RELEVANCE_FLOOR  # filtered — can't confirm same category


def test_floor_value_contract():
    # The floor must sit above same-vertical-different-category (0.25) and the
    # known-wish-unknown-candidate case (0.3), but at/below exact (1.0) and
    # neutral (0.5) so those survive.
    assert 0.3 < MATCH_RELEVANCE_FLOOR <= 0.5
