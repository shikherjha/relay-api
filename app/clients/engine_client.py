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
        """Parity with relay-engine's rule engine for offline/mock runs."""
        grade = req.passport.grade_numeric
        reasons: list[str] = []
        guardrails: list[str] = []

        delivery_km = 5.0
        demand_score = 0.0
        if req.demand:
            demand_score = req.demand.demand_score
            if req.demand.nearest_km:
                delivery_km = req.demand.nearest_km

        if req.transfer_count >= 3:
            guardrails.append(f"chain_depth_cap({req.transfer_count}>=3)")
            channel = "recycle" if grade < 0.3 else "donate" if grade < 0.6 else "refurb"
            reasons.append("transfer cap reached → end-of-cycle channel")
        elif req.return_reason in {"too_small", "too_large", "fit"} and req.exchange_available:
            channel = "exchange"
            reasons.append("size/fit reason + exchange SKU in stock → exchange-first")
        elif grade >= 0.6:
            net_rescue = net_co2_saved("rescue", delivery_km)
            if demand_score > 0 and net_rescue > 0:
                channel = "rescue"
                reasons.append(f"good grade + local demand ({req.demand.open_wish_count} wishes)")
            else:
                channel = "p2p_resale"
                if net_rescue <= 0:
                    guardrails.append("net_carbon_gate(rescue<=0)")
                reasons.append("good grade → p2p")
        elif grade >= 0.3:
            channel = "refurb"
            reasons.append("fair grade → refurb")
        else:
            channel = "donate"
            reasons.append("low grade → donate")

        norm = demand_score / (demand_score + 1.0)
        score = round(min(grade * 0.7 + norm * 0.3, 1.0), 3)
        return DispositionResponse(
            channel=channel, score=score, reasons=reasons,
            guardrails_applied=guardrails, net_co2_saved_kg=net_co2_saved(channel, delivery_km),
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
