"""S3 media storage helper — product images + user-uploaded resale/return media.

Everything is best-effort and side-effect free on failure: if S3 isn't
configured or a call raises, the helpers return ``None`` so callers fall back to
the local ``/static`` mount. Public object URLs are used by default; if the
bucket blocks public reads the helper auto-detects that (one unauthenticated
probe) and switches to long-expiry presigned GET URLs. Force the strategy with
``S3_URL_STRATEGY=public|presigned``.

boto3 is imported lazily so the module is importable even where it's absent.
"""

from __future__ import annotations

import threading

from app.core.config import settings

_lock = threading.Lock()
_client = None
_region: str | None = None
_strategy: str | None = None  # resolved "public" | "presigned" (auto mode)


def s3_configured() -> bool:
    return bool(
        settings.s3_enabled
        and settings.s3_bucket
        and settings.aws_access_key_id
        and settings.aws_secret_access_key
    )


def _make_client(region: str | None):
    import boto3  # lazy: keeps the module importable without boto3
    from botocore.config import Config

    # Fast-fail so a misconfigured bucket can never stall seeding / uploads:
    # short timeouts + few retries → callers fall back to /static quickly.
    cfg = Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2})
    return boto3.client(
        "s3",
        region_name=region or None,
        aws_access_key_id=settings.aws_access_key_id or None,
        aws_secret_access_key=settings.aws_secret_access_key or None,
        config=cfg,
    )


def _boto():
    """Cached S3 client pinned to the bucket's real region (so presigned URLs and
    regional hostnames are valid even if AWS_REGION is slightly off)."""
    global _client, _region
    if _client is not None:
        return _client
    with _lock:
        if _client is not None:
            return _client
        region = settings.aws_region or "us-east-1"
        client = _make_client(region)
        try:
            loc = client.get_bucket_location(Bucket=settings.s3_bucket).get("LocationConstraint")
            real = loc or "us-east-1"  # us-east-1 reports None
            if real != region:
                region = real
                client = _make_client(region)
        except Exception:
            pass  # keep configured region if the probe fails
        _region, _client = region, client
        return _client


def _host_region() -> str:
    return _region or settings.aws_region or "us-east-1"


def public_url(key: str) -> str:
    key = key.lstrip("/")
    if settings.s3_public_base_url:
        return f"{settings.s3_public_base_url.rstrip('/')}/{key}"
    return f"https://{settings.s3_bucket}.s3.{_host_region()}.amazonaws.com/{key}"


def presigned_url(key: str, expiry: int | None = None) -> str:
    return _boto().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key.lstrip("/")},
        ExpiresIn=expiry or settings.s3_presign_expiry_seconds,
    )


def object_exists(key: str) -> bool:
    try:
        _boto().head_object(Bucket=settings.s3_bucket, Key=key.lstrip("/"))
        return True
    except Exception:
        return False


def _public_readable(key: str) -> bool:
    """One unauthenticated GET to decide whether public object URLs resolve."""
    import httpx

    try:
        r = httpx.get(public_url(key), timeout=10.0, follow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False


def resolve_strategy(probe_key: str | None = None) -> str:
    """The active URL strategy. In ``auto`` mode, probe a real object once."""
    global _strategy
    if settings.s3_url_strategy in ("public", "presigned"):
        return settings.s3_url_strategy
    if _strategy is not None:
        return _strategy
    if probe_key is not None:
        _strategy = "public" if _public_readable(probe_key) else "presigned"
        return _strategy
    return "presigned"  # safe default until we have an object to probe


def active_strategy() -> str | None:
    """Resolved strategy (for reporting) without forcing a probe."""
    if settings.s3_url_strategy in ("public", "presigned"):
        return settings.s3_url_strategy
    return _strategy


def url_for(key: str) -> str:
    return public_url(key) if resolve_strategy(key) == "public" else presigned_url(key)


def upload_bytes(key: str, data: bytes, content_type: str) -> str | None:
    """Put bytes and return a resolvable URL, or None on any failure/no-config."""
    if not s3_configured():
        return None
    try:
        _boto().put_object(
            Bucket=settings.s3_bucket, Key=key.lstrip("/"), Body=data, ContentType=content_type,
        )
        return url_for(key)
    except Exception:
        return None


def upload_file_idempotent(local_path, key: str, content_type: str) -> str | None:
    """Upload a local file unless the key already exists; return a URL or None."""
    if not s3_configured():
        return None
    try:
        if not object_exists(key):
            with open(local_path, "rb") as fh:
                _boto().put_object(
                    Bucket=settings.s3_bucket, Key=key.lstrip("/"),
                    Body=fh.read(), ContentType=content_type,
                )
        return url_for(key)
    except Exception:
        return None
