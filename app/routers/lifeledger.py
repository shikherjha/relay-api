from __future__ import annotations

from fastapi import APIRouter

from app.schemas.lifeledger import VerifyResult

router = APIRouter(prefix="/lifeledger", tags=["lifeledger"])


@router.get("/{unit_id}/verify", response_model=VerifyResult)
def verify(unit_id: str) -> VerifyResult:
    # Step 3-4: compare local passport_hash vs on-chain hash (Polygon Amoy).
    return VerifyResult(unit_id=unit_id, verified=False, events=[])
