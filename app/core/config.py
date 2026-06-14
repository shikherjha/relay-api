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

    # Swap to real services once they're live (mock unblocks parallel work).
    use_mock_ml: bool = True
    use_mock_engine: bool = True
    http_timeout_seconds: float = 10.0

    s3_bucket: str = "relay-media"
    aws_region: str = "ap-south-1"

    polygon_rpc_url: str = "https://rpc-amoy.polygon.technology"
    lifeledger_private_key: str = ""
    lifeledger_contract_address: str = ""
    # Real on-chain anchoring requires a funded Amoy key; mock anchors locally.
    use_real_ledger: bool = False

    embedding_dim: int = 384
    rescue_default_radius_km: float = 3.0
    rescue_discount_base: float = 0.15
    rescue_discount_max: float = 0.45
    rescue_user_return_rate_cap: float = 0.4
    chain_depth_cap: int = 3

    # Pillar 5 — green credits buy ACCESS, not discounts. High-credit users see
    # new rescue listings during an embargo window before they go public.
    rescue_early_access_window_seconds: int = 600  # 10-minute head start
    rescue_early_access_credit_threshold: float = 100.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
