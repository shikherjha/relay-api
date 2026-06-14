from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Relay API"
    app_version: str = "0.1.0"
    port: int = 8000

    database_url: str = "postgresql://relay:relay@localhost:5432/relay"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"

    ml_service_url: str = "http://localhost:8001"
    engine_service_url: str = "http://localhost:8002"

    s3_bucket: str = "relay-media"
    aws_region: str = "ap-south-1"

    polygon_rpc_url: str = "https://rpc-amoy.polygon.technology"
    lifeledger_private_key: str = ""

    embedding_dim: int = 384
    rescue_default_radius_km: float = 3.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
