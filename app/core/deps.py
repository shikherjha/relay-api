"""Shared FastAPI dependencies (demo auth + client factories)."""

from __future__ import annotations

from fastapi import Header

from app.clients.engine_client import EngineClient, get_engine_client
from app.clients.ml_client import MLClient, get_ml_client

DEMO_USER_ID = "00000000-0000-0000-0000-000000000001"


def current_user_id(x_user_id: str | None = Header(default=None)) -> str:
    """Demo auth stub — X-User-Id header, falls back to a fixed demo user."""
    return x_user_id or DEMO_USER_ID


def ml_client() -> MLClient:
    return get_ml_client()


def engine_client() -> EngineClient:
    return get_engine_client()
