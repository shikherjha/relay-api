"""Celery app entrypoint (compose runs `celery -A app.worker worker`).

Tasks (grade_return, wishlist match, LifeLedger writes, rescue expiry) are
registered in later phases. T0 only wires the broker.
"""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "relay",
    broker=settings.celery_broker_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
)

# Aliases so `celery -A app.worker worker` auto-discovers the instance.
app = celery_app
celery = celery_app
