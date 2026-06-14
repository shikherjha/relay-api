from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.clients.ml_client import MLClient
from app.core.deps import ml_client
from app.schemas.catalog import Product, ProductDetail
from app.schemas.ml import Vertical

router = APIRouter(prefix="/products", tags=["catalog"])


@router.get("", response_model=list[Product])
def list_products(
    vertical: Vertical | None = Query(default=None),
    category: str | None = Query(default=None),
) -> list[Product]:
    # Step 3 (api-seed): query Postgres catalog.
    return []


@router.get("/{product_id}", response_model=ProductDetail)
def get_product(product_id: str, ml: MLClient = Depends(ml_client)) -> ProductDetail:
    # Step 3 wires DB lookup; fit flags are proxied from relay-ml on the PDP.
    flags = ml.fit_flags(sku_id=product_id, brand=None, category=None)
    return ProductDetail(
        id=product_id,
        sku=f"SKU-{product_id}",
        title="(stub) Product",
        category="tshirt",
        vertical="fashion",
        price=0.0,
        fit_flags=flags,
    )
