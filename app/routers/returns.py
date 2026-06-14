from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, UploadFile

from app.clients.engine_client import EngineClient
from app.core.deps import current_user_id, engine_client
from app.schemas.disposition import DispositionRequest, DispositionResponse, ReturnReason
from app.schemas.ml import ConditionPassport
from app.schemas.returns import MediaAccepted, ReturnCreate, ReturnEvent

router = APIRouter(prefix="/returns", tags=["returns"])


@router.post("", response_model=ReturnEvent, status_code=201)
def create_return(payload: ReturnCreate, user_id: str = Depends(current_user_id)) -> ReturnEvent:
    # Step 3 (api-returns): persist return_event row.
    return ReturnEvent(
        id="stub",
        unit_id=payload.unit_id,
        user_id=payload.user_id or user_id,
        reason_code=payload.reason_code,
        status="initiated",
        created_at=datetime.now(timezone.utc),
    )


@router.post("/{return_id}/media", response_model=MediaAccepted, status_code=202)
async def upload_media(return_id: str, files: list[UploadFile] = File(...)) -> MediaAccepted:
    # Step 3: presigned S3 upload + enqueue grade_return_task (Celery).
    return MediaAccepted(job_id=f"job-{return_id}", status="queued", media_hashes=[])


@router.get("/{return_id}/passport", response_model=ConditionPassport)
def get_passport(return_id: str) -> ConditionPassport:
    # Step 3: read condition_passports row.
    return ConditionPassport(
        unit_id="stub",
        return_id=return_id,
        grade="B+",
        grade_numeric=0.78,
        vertical="fashion",
        confidence=0.0,
        graded_at=datetime.now(timezone.utc),
        model_tier_used="stub",
    )


@router.post("/{return_id}/disposition", response_model=DispositionResponse)
def compute_disposition(
    return_id: str,
    reason_code: ReturnReason = "changed_mind",
    engine: EngineClient = Depends(engine_client),
) -> DispositionResponse:
    # Step 3-4: load passport + demand, then call engine. Stub builds a request.
    passport = ConditionPassport(
        unit_id="stub",
        return_id=return_id,
        grade="B+",
        grade_numeric=0.78,
        vertical="fashion",
        confidence=0.88,
        graded_at=datetime.now(timezone.utc),
        model_tier_used="stub",
    )
    req = DispositionRequest(unit_id="stub", passport=passport, return_reason=reason_code)
    return engine.score_disposition(req)
