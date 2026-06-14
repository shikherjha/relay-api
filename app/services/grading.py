"""Grading orchestration (api-returns spine).

relay-api calls relay-ml to grade, stamps the passport hash, persists the
ConditionPassport, and writes a GRADED LifeLedger event. ML stays behind the
swappable client (mock until Bhavya's /grade-image is live).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.clients.ledger_client import get_ledger_client
from app.clients.ml_client import MLClient
from app.core.hashing import passport_hash
from app.models import entities as m


def grade_and_store(
    db: Session,
    ml: MLClient,
    *,
    return_event: m.ReturnEvent,
    unit: m.ProductUnit,
    image: bytes,
    filename: str,
) -> m.ConditionPassport:
    product = db.get(m.Product, unit.product_id)
    category = product.category if product else "other"

    passport = ml.grade_image(image=image, filename=filename, unit_id=str(unit.id), category=category)
    passport.return_id = str(return_event.id)

    payload = passport.model_dump(mode="json")
    digest = passport_hash(payload)
    payload["passport_hash"] = digest
    passport.passport_hash = digest

    row = m.ConditionPassport(
        unit_id=unit.id, return_id=return_event.id, passport=payload, passport_hash=digest,
    )
    db.add(row)

    unit.status = "graded"
    return_event.status = "graded"

    anchor = get_ledger_client().anchor(unit_id=str(unit.id), passport_hash=digest)
    db.add(m.LifeLedgerEvent(
        unit_id=unit.id, event_type="GRADED", passport_hash=digest, tx_hash=anchor.tx_hash,
    ))

    db.commit()
    db.refresh(row)
    return row
