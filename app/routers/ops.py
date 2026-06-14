from __future__ import annotations

from fastapi import APIRouter

from app.schemas.ops import ChainDepthRow, HighReturnSku, OpsImpact, OpsRescueLive

router = APIRouter(prefix="/ops", tags=["ops"])


@router.get("/high-return-skus", response_model=list[HighReturnSku])
def high_return_skus() -> list[HighReturnSku]:
    # Step 4 (api-seller-signals): aggregate return_event by sku + dominant reason.
    return []


@router.get("/rescue-live", response_model=OpsRescueLive)
def rescue_live() -> OpsRescueLive:
    # Step 4: active rescue listings with live TTL + decay discount.
    return OpsRescueLive(listings=[])


@router.get("/chain-depth", response_model=list[ChainDepthRow])
def chain_depth() -> list[ChainDepthRow]:
    # Step 4: units near/at the transfer cap.
    return []


@router.get("/impact", response_model=OpsImpact)
def ops_impact() -> OpsImpact:
    # Step 4: aggregate impact_events.
    return OpsImpact()
