"""Grading orchestration (api-returns spine).

relay-api calls relay-ml to grade, stamps the passport hash, persists the
ConditionPassport, and writes a GRADED LifeLedger event. ML stays behind the
swappable client (mock until Bhavya's /grade-image is live).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.clients.ledger_client import get_ledger_client
from app.clients.ml_client import MLClient
from app.core.config import settings
from app.core.hashing import passport_hash
from app.models import entities as m
from app.schemas.ml import ConditionPassport, EmbedRequest, Verification


def is_size_reason(reason: str | None) -> bool:
    """A size/fit return (and NOT defective/not_as_described) → pristine asset."""
    return reason in set(settings.size_return_reasons)


def apply_pristine_size_boost(passport: ConditionPassport) -> None:
    """SIZE-RETURN WINS: a size/fit return is pristine — the buyer never used it,
    it just didn't fit. Floor the resale grade to Grade A / "Like New", drop any
    cosmetic defects, and steer disposition toward resell so it re-sells at
    near-original value (minimal discount applied by the listing layer)."""
    if passport.grade_numeric < settings.size_return_pristine_grade_numeric:
        passport.grade = settings.size_return_pristine_grade  # type: ignore[assignment]
        passport.grade_numeric = settings.size_return_pristine_grade_numeric
    passport.defects = []
    passport.packaging_state = "sealed"
    passport.disposition_hint = "p2p_resale"


def ensure_verification(
    passport: ConditionPassport,
    *,
    expected_color: str | None = None,
    expected_size: str | None = None,
    product_title: str | None = None,
) -> None:
    """relay-api fallback: if relay-ml didn't return a verification block, attach a
    best-effort one. relay-api can't see the image, so colour/item match stay
    "unknown" unless the passport already exposes an observed colour."""
    if passport.verification is not None:
        return
    observed = None
    color_match = "unknown"
    if expected_color and observed:
        e, o = expected_color.strip().lower(), observed.strip().lower()
        color_match = "match" if (e == o or e in o or o in e) else "mismatch"
    passport.verification = Verification(
        color_match=color_match, item_match="unknown",
        observed_color=observed, expected_color=expected_color,
    )


def grade_and_store(
    db: Session,
    ml: MLClient,
    *,
    return_event: m.ReturnEvent,
    unit: m.ProductUnit,
    media: list[tuple[str, bytes]],
    is_video: bool = False,
    size: str | None = None,
    media_urls: list[str] | None = None,
    return_reason: str | None = None,
    expected_size: str | None = None,
    expected_color: str | None = None,
    product_title: str | None = None,
) -> m.ConditionPassport:
    """Grade the uploaded media (multi-angle photos or a video) → passport.

    Uses relay-ml's multi-angle / video endpoints when applicable so Bedrock
    assesses all angles holistically. Sends the order's expected size/colour/title
    so the grade also reports an order-vs-item ``verification`` block, applies the
    size-return pristine boost, and stamps an embedding so a freshly-returned unit
    is immediately matchable (next-owner / Genie).
    """
    product = db.get(m.Product, unit.product_id)
    category = product.category if product else "other"
    media = [(name or "upload", b) for name, b in media if b]
    if not media:
        raise ValueError("no media to grade")

    ev = dict(expected_size=expected_size, expected_color=expected_color, product_title=product_title)
    if is_video:
        name, blob = media[0]
        passport = ml.grade_video(video=blob, filename=name, unit_id=str(unit.id), category=category, **ev)
    elif len(media) > 1:
        passport = ml.grade_images(images=media, unit_id=str(unit.id), category=category, **ev)
    else:
        name, blob = media[0]
        passport = ml.grade_image(image=blob, filename=name, unit_id=str(unit.id), category=category, **ev)
    passport.return_id = str(return_event.id)

    # Order-vs-item verification (relay-api fallback if relay-ml omitted it) +
    # the size-return pristine boost — both folded into the passport BEFORE the
    # hash so LifeLedger verification still holds.
    ensure_verification(
        passport, expected_color=expected_color, expected_size=expected_size, product_title=product_title,
    )
    if is_size_reason(return_reason):
        apply_pristine_size_boost(passport)

    payload = passport.model_dump(mode="json")
    # Stamp the uploaded media URLs INTO the payload before hashing so the
    # anchored passport_hash covers them and LifeLedger verification still holds.
    if media_urls:
        payload["media_urls"] = media_urls
    digest = passport_hash(payload)
    payload["passport_hash"] = digest
    passport.passport_hash = digest

    row = m.ConditionPassport(
        unit_id=unit.id, return_id=return_event.id, passport=payload, passport_hash=digest,
    )
    db.add(row)

    unit.status = "graded"
    return_event.status = "graded"

    # Embed the graded unit (category + vertical [+ size]) so it joins the
    # cosine matching pool. Best-effort: matching falls back to category if absent.
    if unit.embedding is None:
        try:
            unit.embedding = ml.embed(EmbedRequest(
                category=category, vertical=passport.vertical, size=size,
            )).vector
        except Exception:  # pragma: no cover - embedding is best-effort
            pass

    anchor = get_ledger_client().anchor(unit_id=str(unit.id), passport_hash=digest)
    db.add(m.LifeLedgerEvent(
        unit_id=unit.id, event_type="GRADED", passport_hash=digest, tx_hash=anchor.tx_hash,
    ))

    db.commit()
    db.refresh(row)
    return row
