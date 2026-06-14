from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.schemas.ml import FitFlagsResponse, Vertical


class Product(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sku: str
    title: str
    category: str
    vertical: Vertical
    price: float
    metadata: dict | None = None


class ProductDetail(Product):
    fit_flags: FitFlagsResponse | None = None
