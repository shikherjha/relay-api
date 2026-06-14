"""Schemas mirroring relay-contracts v1 for the ML boundary.

ConditionPassport, defects, fit flags, plus the embedding + wish-score
endpoints owned by relay-ml (Bhavya). These are the canonical shapes relay-api
persists and forwards.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Grade = Literal["A+", "A", "B+", "B", "C", "D"]
Vertical = Literal["fashion", "electronics"]
DispositionChannel = Literal[
    "exchange", "rescue", "p2p_resale", "refurb", "donate", "recycle", "restock"
]
PackagingState = Literal["sealed", "opened", "damaged", "missing"]
DefectType = Literal[
    "scuff", "crack", "stain", "tear", "dent", "discoloration",
    "missing_part", "screen_damage", "water_damage", "functional_fault", "other",
]
DefectSeverity = Literal["minor", "moderate", "major"]
FitFlagType = Literal["runs_large", "runs_small", "true_to_size", "critical_fit"]
# Order-vs-item verification: does the returned item match what was ordered?
VerificationState = Literal["match", "mismatch", "unknown"]


class Defect(BaseModel):
    type: DefectType
    severity: DefectSeverity
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    confidence: float | None = Field(default=None, ge=0, le=1)
    description: str | None = Field(default=None, max_length=280)


class Verification(BaseModel):
    """Cheap, prompt-only AI check that the graded item matches the order line.

    Produced additively by relay-ml's grade prompt (no extra image / Bedrock
    call) from the order's expected_color / product_title. relay-api fills a
    best-effort fallback when relay-ml omits it. Surfaced on the ConditionPassport
    AND on the ResaleListing so the buyer sees "color/item verified".
    """

    color_match: VerificationState = "unknown"
    item_match: VerificationState = "unknown"
    observed_color: str | None = None
    expected_color: str | None = None


class ConditionPassport(BaseModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    unit_id: str
    return_id: str | None = None
    grade: Grade
    grade_numeric: float = Field(..., ge=0, le=1)
    category: str | None = None
    vertical: Vertical
    disposition_hint: DispositionChannel | None = None
    defects: list[Defect] = Field(default_factory=list)
    packaging_state: PackagingState | None = None
    confidence: float = Field(..., ge=0, le=1)
    media_hashes: list[str] = Field(default_factory=list)
    passport_hash: str | None = None
    graded_at: datetime
    model_tier_used: str
    warranty_months_remaining: int = Field(default=0, ge=0)
    repair_events: list[dict] = Field(default_factory=list)
    # Additive AI order-vs-item verification (optional; None when not assessed).
    verification: Verification | None = None


class FitFlag(BaseModel):
    type: FitFlagType
    message: str = Field(..., max_length=200)
    confidence: float = Field(..., ge=0, le=1)


class FitFlagsResponse(BaseModel):
    sku_id: str
    flags: list[FitFlag]
    source: str = "rules_v1"


class EmbedRequest(BaseModel):
    text: str | None = None
    category: str | None = None
    grade: Grade | None = None
    size: str | None = None
    vertical: Vertical | None = None


class EmbedResponse(BaseModel):
    vector: list[float]
    model: str


class WishScoreRequest(BaseModel):
    wish_age_days: float = Field(..., ge=0)
    user_purchase_count: int = Field(default=0, ge=0)
    category_affinity: float = Field(default=0.0, ge=0, le=1)
    has_fit_profile: bool = False


class WishScoreResponse(BaseModel):
    score: float = Field(..., ge=0, le=1)
    model: str = "logreg_v1"
