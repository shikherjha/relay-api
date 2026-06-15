from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.ml_client import MLClient
from app.core.deps import current_user_id, ml_client
from app.core.ids import to_uuid
from app.db.session import get_db
from app.models import entities as m
from app.schemas.ml import EmbedRequest, WishScoreRequest
from app.schemas.wishlist import Wishlist, WishlistCreate, WishMatch
from app.services.matching import match_for_user

router = APIRouter(prefix="/wishlist", tags=["wishlist"])

DEFAULT_WISH_TTL_DAYS = 30


def _to_wishlist(row: m.ReverseWishlist) -> Wishlist:
    return Wishlist(
        id=str(row.id), user_id=str(row.user_id), category=row.category, size=row.size,
        max_price=float(row.max_price) if row.max_price is not None else None,
        expires_at=row.expires_at, wish_score=row.wish_score,
    )


@router.post("", response_model=Wishlist, status_code=201)
def create_wish(
    payload: WishlistCreate,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
    ml: MLClient = Depends(ml_client),
) -> Wishlist:
    uid = to_uuid(user_id, what="user id")
    user = db.get(m.User, uid)

    embedding = ml.embed(EmbedRequest(category=payload.category, size=payload.size, vertical=None)).vector
    score = ml.wish_score(WishScoreRequest(
        wish_age_days=0.0,
        user_purchase_count=0,
        category_affinity=0.5,
        has_fit_profile=bool(user and user.fit_profile),
    )).score

    row = m.ReverseWishlist(
        user_id=uid, category=payload.category, size=payload.size,
        max_price=payload.max_price,
        geo_lat=payload.geo.lat if payload.geo else None,
        geo_lng=payload.geo.lng if payload.geo else None,
        expires_at=datetime.now(timezone.utc) + timedelta(days=DEFAULT_WISH_TTL_DAYS),
        embedding=embedding, wish_score=score,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_wishlist(row)


@router.get("", response_model=list[Wishlist])
def list_wishes(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)) -> list[Wishlist]:
    """The caller's active (non-expired) wishes — the source of truth for Genie's
    "Your wishes", so a posted wish survives a page reload."""
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(m.ReverseWishlist).where(m.ReverseWishlist.user_id == to_uuid(user_id, what="user id"))
        .order_by(m.ReverseWishlist.created_at.desc())
    ).scalars().all()
    out: list[Wishlist] = []
    for r in rows:
        exp = r.expires_at
        if exp is not None and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp is not None and exp < now:
            continue
        out.append(_to_wishlist(r))
    return out


@router.delete("/{wish_id}", status_code=204, response_class=Response)
def delete_wish(
    wish_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> Response:
    """Drop a wish — only the owner can delete their own."""
    row = db.get(m.ReverseWishlist, to_uuid(wish_id, what="wish id"))
    if row is None or str(row.user_id) != str(to_uuid(user_id, what="user id")):
        raise HTTPException(status_code=404, detail="wish not found")
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.get("/matches", response_model=list[WishMatch])
def wishlist_matches(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
    ml: MLClient = Depends(ml_client),
) -> list[WishMatch]:
    return match_for_user(db, to_uuid(user_id, what="user id"), ml=ml)
