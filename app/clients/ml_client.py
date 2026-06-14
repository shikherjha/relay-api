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
    Verification,
    WishScoreRequest,
    WishScoreResponse,
)
from app.schemas.resale import PriceRange, ResaleAssessment

# Media kind hint for grade_and_price (images = 1-8 angle photos; video = one clip).
ResaleMedia = list[tuple[str, bytes]]


def compute_resale_pricing(
    *, grade_numeric: float, original_price: float, age_days: int
) -> tuple[PriceRange, float]:
    """Deterministic local resale pricer (the swap-ready fallback seam).

    condition_factor = clamp(grade_numeric, min, max)
    age_factor       = max(floor, 1 - age_days / horizon)
    base             = original_price × condition_factor × age_factor
    price_range      = [base × 0.90, base × 1.10]; list_price = mean(range)
    """
    cf = max(settings.resale_condition_factor_min, min(grade_numeric, settings.resale_condition_factor_max))
    af = max(settings.resale_age_factor_floor, 1.0 - (max(0, age_days) / settings.resale_age_horizon_days))
    base = max(0.0, float(original_price or 0.0)) * cf * af
    pmin = round(base * settings.resale_price_band_low, 2)
    pmax = round(base * settings.resale_price_band_high, 2)
    list_price = round((pmin + pmax) / 2, 2)
    return PriceRange(min=pmin, max=pmax), list_price


def assessment_from_passport(
    passport: ConditionPassport, *, original_price: float, age_days: int, source: str
) -> ResaleAssessment:
    price_range, list_price = compute_resale_pricing(
        grade_numeric=passport.grade_numeric, original_price=original_price, age_days=age_days,
    )
    return ResaleAssessment(
        passport=passport,
        resale_grade=passport.grade,
        grade_numeric=passport.grade_numeric,
        original_price=round(float(original_price or 0.0), 2),
        age_days=int(age_days),
        price_range=price_range,
        list_price=list_price,
        pricing_rationale=(
            f"Local pricer · grade {passport.grade} · "
            f"{'nearly new' if age_days <= 30 else f'~{max(1, age_days // 30)} months old'}"
        ),
        source=source,  # type: ignore[arg-type]
    )


def assessment_from_grade_price(
    data: dict, *, original_price: float, age_days: int
) -> ResaleAssessment:
    """Map relay-ml's FLAT /grade-and-price response → our ResaleAssessment.

    Bhavya's endpoint returns a ConditionPassport (flat) plus resale fields:
    ``resale_grade`` (label, e.g. "Very Good"), ``price_range`` {min,max},
    ``currency``, ``pricing_rationale``. It does NOT echo original_price/age_days
    or a nested passport, so we rebuild the passport from the flat fields and
    carry our request's original_price/age_days through.
    """
    # Our ConditionPassport ignores unknown keys (resale_grade/currency/etc).
    passport = ConditionPassport.model_validate(data)
    pr = data.get("price_range") or {}
    pmin = float(pr.get("min", 0.0))
    pmax = float(pr.get("max", pmin))
    list_price = round((pmin + pmax) / 2, 2)
    return ResaleAssessment(
        passport=passport,
        resale_grade=str(data.get("resale_grade") or passport.grade),
        grade_numeric=passport.grade_numeric,
        original_price=round(float(original_price or 0.0), 2),
        age_days=int(age_days),
        price_range=PriceRange(min=round(pmin, 2), max=round(pmax, 2)),
        list_price=list_price,
        pricing_rationale=data.get("pricing_rationale"),
        source="ml",
    )


def _expected_form(
    expected_size: str | None, expected_color: str | None, product_title: str | None
) -> dict:
    """Order-vs-item context as multipart Form fields (omit empties so existing
    relay-ml callers are unaffected)."""
    out: dict[str, str] = {}
    if expected_size:
        out["expected_size"] = expected_size
    if expected_color:
        out["expected_color"] = expected_color
    if product_title:
        out["product_title"] = product_title
    return out


def _mock_verification(
    expected_color: str | None, product_title: str | None
) -> Verification | None:
    """Deterministic mock verification (assumes the right item was photographed)."""
    if not (expected_color or product_title):
        return None
    return Verification(
        color_match="match" if expected_color else "unknown",
        item_match="match" if product_title else "unknown",
        observed_color=expected_color, expected_color=expected_color,
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
    def grade_image(
        self, image: bytes, filename: str, unit_id: str, category: str,
        *, expected_size: str | None = None, expected_color: str | None = None,
        product_title: str | None = None,
    ) -> ConditionPassport: ...
    def grade_images(
        self, images: list[tuple[str, bytes]], unit_id: str, category: str,
        *, expected_size: str | None = None, expected_color: str | None = None,
        product_title: str | None = None,
    ) -> ConditionPassport: ...
    def grade_video(
        self, video: bytes, filename: str, unit_id: str, category: str,
        *, expected_size: str | None = None, expected_color: str | None = None,
        product_title: str | None = None,
    ) -> ConditionPassport: ...
    def grade_and_price(
        self,
        media: ResaleMedia,
        *,
        unit_id: str,
        category: str,
        original_price: float,
        age_days: int,
        vertical: str | None = None,
        is_video: bool = False,
        expected_size: str | None = None,
        expected_color: str | None = None,
        product_title: str | None = None,
    ) -> ResaleAssessment: ...
    def fit_flags(self, sku_id: str, brand: str | None, category: str | None) -> FitFlagsResponse: ...
    def embed(self, req: EmbedRequest) -> EmbedResponse: ...
    def wish_score(self, req: WishScoreRequest) -> WishScoreResponse: ...


class MockMLClient:
    def health(self) -> dict:
        return {"status": "ok", "mock": True}

    def _mock_passport(
        self, *, unit_id: str, category: str, media_hashes: list[str], tier: str
    ) -> ConditionPassport:
        electronics = {"headphones", "smartphone", "laptop", "speaker", "smartwatch", "camera", "keyboard"}
        return ConditionPassport(
            unit_id=unit_id,
            grade="B+",
            grade_numeric=0.78,
            category=category,
            vertical="electronics" if category in electronics else "fashion",
            disposition_hint="p2p_resale",
            packaging_state="opened",
            confidence=0.88,
            media_hashes=media_hashes,
            graded_at=datetime.now(timezone.utc),
            model_tier_used=tier,
        )

    def grade_image(
        self, image: bytes, filename: str, unit_id: str, category: str,
        *, expected_size: str | None = None, expected_color: str | None = None,
        product_title: str | None = None,
    ) -> ConditionPassport:
        media_hash = hashlib.sha256(image).hexdigest() if image else None
        passport = self._mock_passport(
            unit_id=unit_id, category=category,
            media_hashes=[media_hash] if media_hash else [], tier="mock",
        )
        passport.verification = _mock_verification(expected_color, product_title)
        return passport

    def grade_images(
        self, images: list[tuple[str, bytes]], unit_id: str, category: str,
        *, expected_size: str | None = None, expected_color: str | None = None,
        product_title: str | None = None,
    ) -> ConditionPassport:
        hashes = [hashlib.sha256(b).hexdigest() for _, b in images if b]
        passport = self._mock_passport(
            unit_id=unit_id, category=category, media_hashes=hashes,
            tier=f"mock+{len(hashes)}angles",
        )
        passport.verification = _mock_verification(expected_color, product_title)
        return passport

    def grade_video(
        self, video: bytes, filename: str, unit_id: str, category: str,
        *, expected_size: str | None = None, expected_color: str | None = None,
        product_title: str | None = None,
    ) -> ConditionPassport:
        media_hash = hashlib.sha256(video).hexdigest() if video else None
        passport = self._mock_passport(
            unit_id=unit_id, category=category,
            media_hashes=[media_hash] if media_hash else [], tier="mock+keyframes",
        )
        passport.verification = _mock_verification(expected_color, product_title)
        return passport

    def grade_and_price(
        self,
        media: ResaleMedia,
        *,
        unit_id: str,
        category: str,
        original_price: float,
        age_days: int,
        vertical: str | None = None,
        is_video: bool = False,
        expected_size: str | None = None,
        expected_color: str | None = None,
        product_title: str | None = None,
    ) -> ResaleAssessment:
        # Deterministic: real-shaped grade (mock passport) + local pricer.
        ev = dict(expected_size=expected_size, expected_color=expected_color, product_title=product_title)
        if is_video and media:
            name, blob = media[0]
            passport = self.grade_video(video=blob, filename=name, unit_id=unit_id, category=category, **ev)
        elif len(media) > 1:
            passport = self.grade_images(images=media, unit_id=unit_id, category=category, **ev)
        elif media:
            name, blob = media[0]
            passport = self.grade_image(image=blob, filename=name, unit_id=unit_id, category=category, **ev)
        else:
            passport = self._mock_passport(unit_id=unit_id, category=category, media_hashes=[], tier="mock")
            passport.verification = _mock_verification(expected_color, product_title)
        return assessment_from_passport(
            passport, original_price=original_price, age_days=age_days, source="fallback",
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
        # Grading (Bedrock under the hood) can take longer than a plain call.
        self._grade_timeout = settings.ml_grade_timeout_seconds

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(base_url=self._base, timeout=timeout or self._timeout)

    def health(self) -> dict:
        with self._client() as c:
            return c.get("/health").json()

    def grade_image(
        self, image: bytes, filename: str, unit_id: str, category: str,
        *, expected_size: str | None = None, expected_color: str | None = None,
        product_title: str | None = None,
    ) -> ConditionPassport:
        with self._client(self._grade_timeout) as c:
            resp = c.post(
                "/grade-image",
                data={"unit_id": unit_id, "category": category,
                      **_expected_form(expected_size, expected_color, product_title)},
                files={"image": (filename, image)},
            )
            resp.raise_for_status()
            return ConditionPassport.model_validate(resp.json())

    def grade_images(
        self, images: list[tuple[str, bytes]], unit_id: str, category: str,
        *, expected_size: str | None = None, expected_color: str | None = None,
        product_title: str | None = None,
    ) -> ConditionPassport:
        # relay-ml /grade-images expects the field name `images` repeated (1-8 files);
        # always Bedrock multi-angle on Bhavya's side.
        with self._client(self._grade_timeout) as c:
            resp = c.post(
                "/grade-images",
                data={"unit_id": unit_id, "category": category,
                      **_expected_form(expected_size, expected_color, product_title)},
                files=[("images", (name, b)) for name, b in images],
            )
            resp.raise_for_status()
            return ConditionPassport.model_validate(resp.json())

    def grade_video(
        self, video: bytes, filename: str, unit_id: str, category: str,
        *, expected_size: str | None = None, expected_color: str | None = None,
        product_title: str | None = None,
    ) -> ConditionPassport:
        with self._client(self._grade_timeout) as c:
            resp = c.post(
                "/grade-video",
                data={"unit_id": unit_id, "category": category,
                      **_expected_form(expected_size, expected_color, product_title)},
                files={"video": (filename, video)},
            )
            resp.raise_for_status()
            return ConditionPassport.model_validate(resp.json())

    def _grade_media(
        self, media: ResaleMedia, *, unit_id: str, category: str, is_video: bool,
        expected_size: str | None = None, expected_color: str | None = None,
        product_title: str | None = None,
    ) -> ConditionPassport:
        """Dispatch to the right real grading endpoint (Bedrock under the hood)."""
        ev = dict(expected_size=expected_size, expected_color=expected_color, product_title=product_title)
        if is_video and media:
            name, blob = media[0]
            return self.grade_video(video=blob, filename=name, unit_id=unit_id, category=category, **ev)
        if len(media) > 1:
            return self.grade_images(images=media, unit_id=unit_id, category=category, **ev)
        name, blob = media[0]
        return self.grade_image(image=blob, filename=name, unit_id=unit_id, category=category, **ev)

    def grade_and_price(
        self,
        media: ResaleMedia,
        *,
        unit_id: str,
        category: str,
        original_price: float,
        age_days: int,
        vertical: str | None = None,
        is_video: bool = False,
        expected_size: str | None = None,
        expected_color: str | None = None,
        product_title: str | None = None,
    ) -> ResaleAssessment:
        """Grade + price for resale via Bhavya's relay-ml ``POST /grade-and-price``.

        Real contract (relay-ml main): multipart with ``images`` (1-8, repeated)
        OR a single ``video`` file, plus Form fields ``unit_id``, ``category``,
        ``original_price``, ``age_days``, ``vertical``. Response is a FLAT
        ConditionPassport + ``resale_grade`` (label), ``price_range`` {min,max},
        ``currency``, ``pricing_rationale`` — mapped via ``assessment_from_grade_price``.

        Safety net: on ANY error from the endpoint (not-implemented, 5xx,
        connection, or an unparseable body) we fall back to the deterministic
        local pricer on top of a real/mock grade, so a resell/relist never breaks.
        """
        data = {
            "unit_id": unit_id,
            "category": category,
            "original_price": str(original_price),
            "age_days": str(age_days),
            "vertical": vertical or "",
            **_expected_form(expected_size, expected_color, product_title),
        }
        if is_video and media:
            name, blob = media[0]
            files = [("video", (name, blob, "video/mp4"))]
        else:
            files = [("images", (name, b, "image/jpeg")) for name, b in media]
        try:
            with self._client(self._grade_timeout) as c:
                resp = c.post("/grade-and-price", data=data, files=files)
            resp.raise_for_status()
            return assessment_from_grade_price(
                resp.json(), original_price=original_price, age_days=age_days,
            )
        except Exception:
            # Endpoint missing / erroring / unparseable → real-or-mock grade + local price.
            pass

        passport = self._grade_media(
            media, unit_id=unit_id, category=category, is_video=is_video,
            expected_size=expected_size, expected_color=expected_color, product_title=product_title,
        )
        return assessment_from_passport(
            passport, original_price=original_price, age_days=age_days, source="fallback",
        )

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
