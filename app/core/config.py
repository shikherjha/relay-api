from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Relay API"
    app_version: str = "0.1.0"
    port: int = 8010

    database_url: str = "postgresql://relay:relay@localhost:5432/relay"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"

    ml_service_url: str = "http://localhost:8001"
    engine_service_url: str = "http://localhost:8002"

    # Extra browser origins allowed to call the API (comma-separated). Local dev
    # origins are always allowed; in production set this to the app domain, e.g.
    # CORS_ALLOW_ORIGINS=https://app.yourdomain.com
    cors_allow_origins: str = ""

    # Swap to real services once they're live (mock unblocks parallel work).
    use_mock_ml: bool = True
    use_mock_engine: bool = True
    http_timeout_seconds: float = 10.0
    # grade / grade-and-price can hit Bedrock end-to-end → allow a longer read.
    ml_grade_timeout_seconds: float = 60.0

    # ── AWS S3 media storage (product images + user uploads) ────────────────
    # Bucket/region come from S3_BUCKET / AWS_REGION; creds from AWS_ACCESS_KEY_ID
    # / AWS_SECRET_ACCESS_KEY (also picked up by boto3's default chain).
    s3_bucket: str = "relay-media"
    aws_region: str = "ap-south-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    # Master switch — auto-noops (falls back to /static) if creds/bucket missing.
    s3_enabled: bool = True
    # URL strategy: "auto" probes a public GET once on first upload and falls back
    # to presigned GET URLs if the bucket blocks public reads. Force with
    # "public" | "presigned".
    s3_url_strategy: Literal["auto", "public", "presigned"] = "auto"
    s3_presign_expiry_seconds: int = 604800  # 7 days (SigV4 max)
    s3_public_base_url: str = ""  # optional CDN/override base (else regional S3 host)
    # Media key prefixes so objects are organised in the bucket.
    s3_product_prefix: str = "products"
    s3_resale_prefix: str = "resale"
    s3_returns_prefix: str = "returns"

    polygon_rpc_url: str = "https://rpc-amoy.polygon.technology"
    lifeledger_private_key: str = ""
    lifeledger_contract_address: str = ""
    # Real on-chain anchoring requires a funded Amoy key; mock anchors locally.
    use_real_ledger: bool = False
    # Block-explorer base + network label for anchored-tx links in the UI.
    lifeledger_explorer_base_url: str = "https://amoy.polygonscan.com"
    lifeledger_network: str = "Polygon Amoy"

    embedding_dim: int = 384
    rescue_default_radius_km: float = 3.0
    rescue_discount_base: float = 0.15
    rescue_discount_max: float = 0.45
    rescue_user_return_rate_cap: float = 0.4
    chain_depth_cap: int = 3

    # Pillar 5 — green credits buy ACCESS, not discounts. High-credit users see
    # new rescue listings during an embargo window before they go public.
    # Tiered: each tier gets a head start; a listing goes public after the
    # longest head start elapses. "silver" = base tier, "gold" = top tier.
    rescue_early_access_window_seconds: int = 600  # silver: 10-minute head start
    rescue_early_access_credit_threshold: float = 100.0  # silver threshold
    rescue_early_access_gold_window_seconds: int = 1800  # gold: 30-minute head start
    rescue_early_access_gold_threshold: float = 300.0  # gold threshold

    # Two-path disposition.
    # Path A (local): pickup-anchored, time-decayed hyperlocal rescue listing.
    # The decay clock starts at courier pickup, not at return request.
    rescue_local_ttl_hours: float = 72.0
    rescue_pickup_anchored: bool = True
    # Path B (national): warehouse "Certified Second-Life" relist used when there
    # is no local taker; shipped nationwide at a flat discount, no time decay.
    rescue_national_enabled: bool = True
    rescue_national_discount_pct: float = 0.30
    # Local pickup keeps the carbon win local → it earns richer credits than a
    # shipped national fulfillment.
    credit_locality_multiplier: float = 1.25

    # ── Rescue Dispatch Score (§21.4) ────────────────────────────────────────
    # Per-viewer edge-utility weights. Positives ideally sum to 1.0; the two risk
    # terms subtract. MUST mirror relay-engine config (the mock engine uses these
    # so engine/fallback stay in parity).
    dispatch_w_demand: float = 0.28
    dispatch_w_distance: float = 0.18
    dispatch_w_ttl: float = 0.12
    dispatch_w_price: float = 0.12
    dispatch_w_keep: float = 0.15
    dispatch_w_carbon: float = 0.15
    dispatch_w_fail_risk: float = 0.5
    dispatch_w_chain_risk: float = 0.2
    # kg net CO2 that maps to a full carbon term; national relist last-mile est.
    dispatch_carbon_norm_kg: float = 3.0
    dispatch_national_delivery_km: float = 40.0
    # Hybrid early access: a strong-wish, in-radius buyer earns up to this extra
    # lead (seconds) on top of their credit tier — best-matched first, not just
    # highest-credit. Gated by the wish-match floor.
    dispatch_early_access_lead_seconds: int = 1800
    dispatch_wish_match_floor: float = 0.45

    # ── Track B "Second Life" resell/republish ──────────────────────────────
    # Return window: a delivered order line is *returnable* while inside it and
    # *resellable* once it expires (the owner can re-list it for a second life).
    return_window_days: int = 7

    # Resale pricing fallback (used when relay-ml /grade-and-price is not live).
    # condition_factor = clamp(grade_numeric, min, max); age_factor decays the
    # value toward a floor as the unit ages; base = orig × condition × age, and
    # the listing price band is base ± a spread, list_price = mean(range).
    resale_condition_factor_min: float = 0.30
    resale_condition_factor_max: float = 0.95
    resale_age_factor_floor: float = 0.45
    resale_age_horizon_days: int = 720
    resale_price_band_low: float = 0.90  # range min = base × 0.90
    resale_price_band_high: float = 1.10  # range max = base × 1.10
    # Resell media bounds (1-8 images, or a single video).
    resale_max_images: int = 8

    # Wishlist price-fit band: a wish is a "price fit" when its max_price sits
    # within this band above the listing's list_price (a snug budget match).
    price_fit_band: float = 0.15

    # ── Return-grading decisions (size pristine boost, exchange, match gate) ──
    # A size/fit return is a PRISTINE asset: the buyer never used it, it just
    # didn't fit. Floor its resale grade to "Like New" (Grade A) and discount it
    # only minimally so it re-sells at near-original value.
    size_return_reasons: tuple[str, ...] = ("too_small", "too_large", "fit")
    size_return_pristine_grade: str = "A"
    size_return_pristine_grade_numeric: float = 0.9
    # Minimal markdowns: a pristine size return / in-window exchange unit is
    # re-listed at ~original price (small nudge to clear locally before pickup).
    size_return_minimal_discount_pct: float = 0.07
    exchange_minimal_discount_pct: float = 0.05

    # Next-owner matching size gate (PRD §"size match OR fit confidence"): a
    # candidate only passes if its size equals the wish size OR the wisher has a
    # confident fit profile for that axis (so we can trust an inexact size).
    matching_fit_confidence_threshold: float = 0.7
    matching_fit_profile_confidence: float = 0.8  # confidence granted by a stored fit profile

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
