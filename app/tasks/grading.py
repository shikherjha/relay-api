"""Async grading task. The media endpoint grades synchronously for demo
determinism (no worker dependency); flip to `.delay(...)` to go async."""

from __future__ import annotations

from app.clients.ml_client import get_ml_client
from app.db.session import SessionLocal
from app.models import entities as m
from app.services.grading import grade_and_store
from app.worker import celery_app


@celery_app.task(name="grade_return")
def grade_return_task(return_id: str, unit_id: str, image_b64: str, filename: str) -> str | None:
    import base64

    db = SessionLocal()
    try:
        return_event = db.get(m.ReturnEvent, return_id)
        unit = db.get(m.ProductUnit, unit_id)
        if return_event is None or unit is None:
            return None
        row = grade_and_store(
            db, get_ml_client(),
            return_event=return_event, unit=unit,
            image=base64.b64decode(image_b64), filename=filename,
        )
        return str(row.id)
    finally:
        db.close()
