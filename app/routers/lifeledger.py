from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.clients.ledger_client import explorer_tx_url
from app.core.config import settings
from app.core.hashing import passport_hash as compute_hash
from app.core.ids import to_uuid
from app.db.session import get_db
from app.models import entities as m
from app.schemas.lifeledger import LifeLedgerEvent, VerifyResult

router = APIRouter(prefix="/lifeledger", tags=["lifeledger"])


@router.get("/{unit_id}/verify", response_model=VerifyResult)
def verify(unit_id: str, db: Session = Depends(get_db)) -> VerifyResult:
    uid = to_uuid(unit_id, what="unit id")
    events = db.execute(
        select(m.LifeLedgerEvent).where(m.LifeLedgerEvent.unit_id == uid)
        .order_by(desc(m.LifeLedgerEvent.created_at))
    ).scalars().all()
    passport = db.execute(
        select(m.ConditionPassport).where(m.ConditionPassport.unit_id == uid)
        .order_by(desc(m.ConditionPassport.graded_at))
    ).scalars().first()

    # Tamper-evidence: recompute the hash from the stored passport JSON and
    # compare it to what was anchored on-chain (mock or real).
    recomputed = compute_hash(passport.passport) if passport else None
    anchored = next((e for e in events if e.passport_hash), None)
    on_chain_hash = anchored.passport_hash if anchored else None
    tx_hash = anchored.tx_hash if anchored else None
    verified = bool(recomputed and on_chain_hash and recomputed == on_chain_hash)

    # Product context + every user-uploaded image (return grading media folded
    # into the passport, plus any resale/relist media) so this page works as the
    # product page too. De-dupe while preserving order.
    unit = db.get(m.ProductUnit, uid)
    product = db.get(m.Product, unit.product_id) if unit is not None else None
    grade = None
    media: list[str] = []
    if passport is not None and isinstance(passport.passport, dict):
        grade = passport.passport.get("grade")
        media.extend(passport.passport.get("media_urls") or [])
    for listing in db.execute(
        select(m.ResaleListing).where(m.ResaleListing.unit_id == uid)
        .order_by(m.ResaleListing.created_at.desc())
    ).scalars().all():
        media.extend(listing.media_urls or [])
    seen: set[str] = set()
    media_urls = [u for u in media if u and not (u in seen or seen.add(u))]

    top_explorer = explorer_tx_url(tx_hash)
    return VerifyResult(
        unit_id=unit_id, verified=verified,
        passport_hash=recomputed, on_chain_hash=on_chain_hash, tx_hash=tx_hash,
        on_chain=top_explorer is not None,
        network=settings.lifeledger_network if settings.use_real_ledger else None,
        explorer_url=top_explorer,
        events=[
            LifeLedgerEvent(
                event_type=e.event_type, tx_hash=e.tx_hash,
                passport_hash=e.passport_hash, created_at=e.created_at,
                explorer_url=explorer_tx_url(e.tx_hash),
            )
            for e in events
        ],
        title=product.title if product else None,
        category=product.category if product else None,
        vertical=product.vertical if product else None,
        image_url=product.image_url if product else None,
        grade=grade,
        media_urls=media_urls,
    )
