"""relay-ml client — Protocol + Mock + HTTP impls.

Shikher consumes relay-ml strictly over HTTP. Mock unblocks the full flow
before Bhavya's service URL is live; swap via `USE_MOCK_ML=false` + `ML_SERVICE_URL`.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Protocol

import httpx

from app.core.config import settings
from app.schemas.ml import (
    ConditionPassport,
    EmbedRequest,
    EmbedResponse,
    FitFlag,
    FitFlagsResponse,
    WishScoreRequest,
    WishScoreResponse,
)


def _deterministic_vector(seed_text: str, dim: int) -> list[float]:
    """Stable pseudo-vector from text — placeholder until /embed is live."""
    out: list[float] = []
    counter = 0
    while len(out) < dim:
        digest = hashlib.sha256(f"{seed_text}:{counter}".encode()).digest()
        for b in digest:
            out.append((b / 255.0) * 2 - 1)  # [-1, 1]
            if len(out) >= dim:
                break
        counter += 1
    norm = sum(v * v for v in out) ** 0.5 or 1.0
    return [v / norm for v in out]


class MLClient(Protocol):
    def health(self) -> dict: ...
    def grade_image(self, image: bytes, filename: str, unit_id: str, category: str) -> ConditionPassport: ...
    def fit_flags(self, sku_id: str, brand: str | None, category: str | None) -> FitFlagsResponse: ...
    def embed(self, req: EmbedRequest) -> EmbedResponse: ...
    def wish_score(self, req: WishScoreRequest) -> WishScoreResponse: ...


class MockMLClient:
    def health(self) -> dict:
        return {"status": "ok", "mock": True}

    def grade_image(self, image: bytes, filename: str, unit_id: str, category: str) -> ConditionPassport:
        media_hash = hashlib.sha256(image).hexdigest() if image else None
        return ConditionPassport(
            unit_id=unit_id,
            grade="B+",
            grade_numeric=0.78,
            category=category,
            vertical="electronics" if category in {"headphones", "smartphone", "laptop"} else "fashion",
            disposition_hint="p2p_resale",
            confidence=0.88,
            media_hashes=[media_hash] if media_hash else [],
            graded_at=datetime.now(timezone.utc),
            model_tier_used="mock",
        )

    def fit_flags(self, sku_id: str, brand: str | None, category: str | None) -> FitFlagsResponse:
        flag = FitFlag(type="true_to_size", message="Most buyers find this true to size.", confidence=0.7)
        return FitFlagsResponse(sku_id=sku_id, flags=[flag], source="mock")

    def embed(self, req: EmbedRequest) -> EmbedResponse:
        seed = req.text or f"{req.category}|{req.grade}|{req.size}|{req.vertical}"
        return EmbedResponse(vector=_deterministic_vector(seed, settings.embedding_dim), model="mock-embed")

    def wish_score(self, req: WishScoreRequest) -> WishScoreResponse:
        recency = max(0.0, 1 - req.wish_age_days / 30.0)
        score = 0.45 * recency + 0.25 * min(req.user_purchase_count / 5.0, 1.0) \
            + 0.2 * req.category_affinity + 0.1 * (1.0 if req.has_fit_profile else 0.0)
        return WishScoreResponse(score=round(min(score, 1.0), 3), model="mock-logreg")


class HTTPMLClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self._base = (base_url or settings.ml_service_url).rstrip("/")
        self._timeout = timeout or settings.http_timeout_seconds

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self._base, timeout=self._timeout)

    def health(self) -> dict:
        with self._client() as c:
            return c.get("/health").json()

    def grade_image(self, image: bytes, filename: str, unit_id: str, category: str) -> ConditionPassport:
        with self._client() as c:
            resp = c.post(
                "/grade-image",
                data={"unit_id": unit_id, "category": category},
                files={"image": (filename, image)},
            )
            resp.raise_for_status()
            return ConditionPassport.model_validate(resp.json())

    def fit_flags(self, sku_id: str, brand: str | None, category: str | None) -> FitFlagsResponse:
        with self._client() as c:
            resp = c.post("/fit-flags", json={"sku_id": sku_id, "brand": brand, "category": category})
            resp.raise_for_status()
            return FitFlagsResponse.model_validate(resp.json())

    def embed(self, req: EmbedRequest) -> EmbedResponse:
        with self._client() as c:
            resp = c.post("/embed", json=req.model_dump(exclude_none=True))
            resp.raise_for_status()
            return EmbedResponse.model_validate(resp.json())

    def wish_score(self, req: WishScoreRequest) -> WishScoreResponse:
        with self._client() as c:
            resp = c.post("/wish-score", json=req.model_dump())
            resp.raise_for_status()
            return WishScoreResponse.model_validate(resp.json())


def get_ml_client() -> MLClient:
    return MockMLClient() if settings.use_mock_ml else HTTPMLClient()
