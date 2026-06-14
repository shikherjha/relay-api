from __future__ import annotations

import uuid

from fastapi import HTTPException


def to_uuid(value: str, *, what: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail=f"invalid {what}")
