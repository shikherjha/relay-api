"""relay-engine client — Protocol + Mock + HTTP impls.

The Go engine is authoritative for disposition + matching. Mock mirrors the
§9 rule matrix so the API flow works before the Go logic lands; swap via
`USE_MOCK_ENGINE=false` + `ENGINE_SERVICE_URL`.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from app.core.carbon import net_co2_saved
from app.core.config import settings
from app.schemas.disposition import DispositionRequest, DispositionResponse


class EngineClient(Protocol):
    def health(self) -> dict: ...
    def score_disposition(self, req: DispositionRequest) -> DispositionResponse: ...


class MockEngineClient:
    def health(self) -> dict:
        return {"status": "ok", "mock": True}

    def score_disposition(self, req: DispositionRequest) -> DispositionResponse:
        passport = req.passport
        reasons: list[str] = []
        guardrails: list[str] = []

        # Chain-depth cap is a hard guardrail (mirrors §7).
        # transfer_count isn't in the passport; engine reads it from DB in the
        # real impl. Mock leans on grade + reason only.
        if req.return_reason in {"too_small", "too_large", "fit"}:
            channel = "exchange"
            reasons.append("size/fit reason → exchange-first")
        elif passport.grade_numeric >= 0.6:
            if req.demand and req.demand.demand_score > 0:
                channel = "rescue"
                reasons.append(f"good grade + local demand ({req.demand.open_wish_count} wishes)")
            else:
                channel = "p2p_resale"
                reasons.append("good grade, no local demand → p2p")
        elif passport.grade_numeric >= 0.3:
            channel = "refurb"
            reasons.append("fair grade → refurb")
        else:
            channel = "donate"
            reasons.append("low grade → donate/recycle")

        delivery_km = 0.0
        if req.geo is not None:
            delivery_km = 5.0  # placeholder; real engine computes haversine
        return DispositionResponse(
            channel=channel,
            score=round(min(passport.grade_numeric + 0.1, 1.0), 3),
            reasons=reasons,
            guardrails_applied=guardrails,
            net_co2_saved_kg=net_co2_saved(channel, delivery_km),
        )


class HTTPEngineClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self._base = (base_url or settings.engine_service_url).rstrip("/")
        self._timeout = timeout or settings.http_timeout_seconds

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self._base, timeout=self._timeout)

    def health(self) -> dict:
        with self._client() as c:
            return c.get("/health").json()

    def score_disposition(self, req: DispositionRequest) -> DispositionResponse:
        with self._client() as c:
            resp = c.post("/disposition/score", json=req.model_dump(mode="json"))
            resp.raise_for_status()
            return DispositionResponse.model_validate(resp.json())


def get_engine_client() -> EngineClient:
    return MockEngineClient() if settings.use_mock_engine else HTTPEngineClient()
