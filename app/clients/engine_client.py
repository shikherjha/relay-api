"""relay-engine client — Protocol + Mock + HTTP impls.

The Go engine is authoritative for disposition + matching. Mock mirrors the
§9 rule matrix so the API flow works before the Go logic lands; swap via
`USE_MOCK_ENGINE=false` + `ENGINE_SERVICE_URL`.
"""

from __future__ import annotations

import logging
import math
from typing import Protocol

import httpx

from app.core.carbon import net_co2_saved
from app.core.config import settings
from app.schemas.dispatch import (
    DispatchCandidate,
    DispatchReason,
    DispatchRequest,
    DispatchResponse,
    DispatchScore,
    DispatchViewer,
)
from app.schemas.disposition import DispositionRequest, DispositionResponse

logger = logging.getLogger(__name__)


def _round3(v: float) -> float:
    """Half-up to 3dp for non-negative scores (parity with Go round3)."""
    return math.floor(v * 1000 + 0.5) / 1000


def _clamp01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def _saturate(x: float) -> float:
    return x / (x + 1.0)


def _score_dispatch_candidate(
    viewer: DispatchViewer, c: DispatchCandidate
) -> tuple[float, list[DispatchReason]]:
    """Pure Rescue Dispatch Score for one edge — MUST mirror
    relay-engine/internal/scoring/dispatch.go so the mock and the Go engine agree."""
    # demand_intent — open-wish demand near the unit, lifted by the viewer's match.
    demand_score = c.demand.demand_score if c.demand else 0.0
    demand_intent = _clamp01(_saturate(demand_score))
    if c.viewer_wish_match > demand_intent:
        demand_intent = _clamp01(c.viewer_wish_match)

    # distance_savings — a closer local pickup saves more last-mile carbon.
    if c.distance_km is None:
        distance_savings = 0.1  # ships (national) → minimal local saving
    else:
        radius = c.radius_km if c.radius_km > 0 else 15.0
        distance_savings = _clamp01(1.0 - c.distance_km / radius)

    # ttl_urgency — a decaying local listing nearing expiry should clear now.
    ttl_urgency = 0.0 if c.ttl_remaining_frac is None else _clamp01(1.0 - c.ttl_remaining_frac)

    # price_acceptance — snug budget fit wins; else markdown depth.
    max_disc = settings.rescue_discount_max or 0.45
    price_acceptance = 1.0 if c.price_fit else _clamp01(c.discount_pct / max_disc) * 0.8

    # buyer_keep_probability — good grade + right size ⇒ unlikely to re-return.
    keep = c.grade_numeric * (1.0 if c.size_fit else 0.85)
    keep = _clamp01(keep)

    # carbon_saved — channel net of last-mile, normalized.
    channel = c.channel or "rescue"
    carbon_norm = _clamp01(net_co2_saved(channel, c.delivery_km) / settings.dispatch_carbon_norm_kg)

    # failed_claim_risk — eligibility / high historical return rate.
    fail_risk = 0.0
    if not viewer.eligible:
        fail_risk = 1.0
    if viewer.return_rate >= settings.rescue_user_return_rate_cap and fail_risk < 0.6:
        fail_risk = 0.6

    # chain_depth_risk — diminishing returns recirculating a well-travelled unit.
    chain_risk = _clamp01(c.transfer_count / float(settings.chain_depth_cap))

    score = (
        settings.dispatch_w_demand * demand_intent
        + settings.dispatch_w_distance * distance_savings
        + settings.dispatch_w_ttl * ttl_urgency
        + settings.dispatch_w_price * price_acceptance
        + settings.dispatch_w_keep * keep
        + settings.dispatch_w_carbon * carbon_norm
        - settings.dispatch_w_fail_risk * fail_risk
        - settings.dispatch_w_chain_risk * chain_risk
    )

    reasons = _dispatch_reasons(
        c, demand_intent, distance_savings, ttl_urgency, price_acceptance, keep,
        carbon_norm, fail_risk, chain_risk,
    )
    return _clamp01(score), reasons


def _dispatch_reasons(
    c: DispatchCandidate, demand: float, dist: float, ttl: float, price: float,
    keep: float, carbon_n: float, fail_risk: float, chain_risk: float,
) -> list[DispatchReason]:
    """Explainable chips, capped at three (scannable card). Positives lead, but
    triggered risk chips reserve slots so a guardrail caveat is never dropped."""
    floor = settings.dispatch_wish_match_floor
    positives = [
        (c.viewer_wish_match >= floor, "matches_your_wish", "Matches your wish"),
        (c.distance_km is not None and dist >= 0.55 and (demand >= 0.45 or c.viewer_wish_match >= 0.3),
         "best_local_fit", "Best local fit"),
        (ttl >= 0.6, "ttl_urgent", "Clearing soon"),
        (c.price_fit, "price_fit", "In your budget"),
        (not c.price_fit and price >= 0.6, "priced_to_clear", "Priced to clear"),
        (carbon_n >= 0.6, "high_carbon_save", "High carbon save"),
        (keep >= 0.85, "high_keep", "Great condition"),
    ]
    risks = [
        (fail_risk >= 0.6, "claim_risk", "Eligibility limits this"),
        (chain_risk >= 0.66, "chain_depth", "Near reuse limit"),
    ]
    risk_out = [DispatchReason(code=code, label=label) for ok, code, label in risks if ok]
    pos_budget = max(0, 3 - len(risk_out))
    pos_out: list[DispatchReason] = []
    for ok, code, label in positives:
        if ok:
            pos_out.append(DispatchReason(code=code, label=label))
            if len(pos_out) >= pos_budget:
                break
    return (pos_out + risk_out)[:3]


class EngineClient(Protocol):
    def health(self) -> dict: ...
    def score_disposition(self, req: DispositionRequest) -> DispositionResponse: ...
    def score_dispatch(self, req: DispatchRequest) -> DispatchResponse: ...


class MockEngineClient:
    def health(self) -> dict:
        return {"status": "ok", "mock": True}

    def score_dispatch(self, req: DispatchRequest) -> DispatchResponse:
        """Parity with relay-engine's dispatch scorer for offline/mock runs."""
        scores = [
            DispatchScore(
                listing_id=c.listing_id,
                dispatch_score=_round3(s),
                dispatch_reasons=reasons,
            )
            for c in req.candidates
            for s, reasons in (_score_dispatch_candidate(req.viewer, c),)
        ]
        return DispatchResponse(scores=scores)

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

    def score_dispatch(self, req: DispatchRequest) -> DispatchResponse:
        """Score the rescue feed via the Go engine; the rescue feed is a hot,
        always-on path, so a transport/parse fault falls back to the local
        deterministic scorer (same formula) rather than 500-ing the feed."""
        try:
            with self._client() as c:
                resp = c.post("/dispatch/score", json=req.model_dump(mode="json"))
                resp.raise_for_status()
                return DispatchResponse.model_validate(resp.json())
        except Exception:  # noqa: BLE001 - engine down/erroring → deterministic fallback
            logger.info("dispatch score via engine unavailable; using local fallback", exc_info=True)
            return MockEngineClient().score_dispatch(req)


def get_engine_client() -> EngineClient:
    return MockEngineClient() if settings.use_mock_engine else HTTPEngineClient()
