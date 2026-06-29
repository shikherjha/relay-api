from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.ml_client import MLClient
from app.core.deps import current_user_id, ml_client
from app.db.session import get_db
from app.models import entities as m
from app.schemas.catalog import Product, ProductDetail
from app.schemas.ml import Vertical
from app.schemas.return_confidence import ReturnConfidence
from app.services.return_confidence import compute_for_product

router = APIRouter(prefix="/products", tags=["catalog"])


def _to_product(row: m.Product) -> dict:
    return {
        "id": str(row.id),
        "sku": row.sku,
        "title": row.title,
        "category": row.category,
        "vertical": row.vertical,
        "price": float(row.price),
        "image_url": row.image_url,
        "metadata": row.product_metadata,
    }


@router.get("", response_model=list[Product])
def list_products(
    vertical: Vertical | None = Query(default=None),
    category: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[Product]:
    stmt = select(m.Product)
    if vertical:
        stmt = stmt.where(m.Product.vertical == vertical)
    if category:
        stmt = stmt.where(m.Product.category == category)
    rows = db.execute(stmt).scalars().all()
    return [Product(**_to_product(r)) for r in rows]


@router.get("/{product_id}", response_model=ProductDetail)
def get_product(
    product_id: str,
    db: Session = Depends(get_db),
    ml: MLClient = Depends(ml_client),
) -> ProductDetail:
    row = db.get(m.Product, product_id)
    if row is None:
        raise HTTPException(status_code=404, detail="product not found")
    brand = (row.product_metadata or {}).get("brand") if row.product_metadata else None
    flags = ml.fit_flags(sku_id=row.sku, brand=brand, category=row.category)
    return ProductDetail(**_to_product(row), fit_flags=flags)


@router.get("/{product_id}/return-confidence", response_model=ReturnConfidence)
def product_return_confidence(
    product_id: str,
    size: str | None = Query(default=None),
    profile_id: str | None = Query(default=None),
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
    ml: MLClient = Depends(ml_client),
) -> ReturnConfidence:
    """Return Confidence for a single product/size on the PDP. Enriches the
    deterministic signals with relay-ml fit flags best-effort — prevention must
    never hard-depend on the ML service being up. `profile_id` scores it for the
    selected Fit Profile (who the shopper is buying for)."""
    row = db.get(m.Product, product_id)
    if row is None:
        raise HTTPException(status_code=404, detail="product not found")
    flags: list[str] = []
    try:
        brand = (row.product_metadata or {}).get("brand") if row.product_metadata else None
        ff = ml.fit_flags(sku_id=row.sku, brand=brand, category=row.category)
        flags = [f.type for f in ff.flags]
    except Exception:  # noqa: BLE001 - prevention degrades gracefully without ML
        flags = []
    return compute_for_product(db, user_id, row, size, profile_id=profile_id, fit_flags=flags)
