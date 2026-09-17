"""Proactive intelligence scan status (Stage 34, Part E10).

Read-only view over the durable ORGANISATION_INTELLIGENCE_SCAN history:
reports the organisation's latest successful scan (completion time,
observed source watermark, signal counts, newly detected signal IDs).

Only backend-persisted scan state is exposed.  Nothing here invents
timestamps or "detected just now" text: every field comes from a
SUCCEEDED job row's result_summary, and an org with no usable scan
history reports scanned=false.
"""

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.auth import Authorisation, require_permission
from app.api.jobs import get_job_repository
from app.auth.models import Permission
from app.models.background_job import BackgroundJobStatus, BackgroundJobType
from app.services.proactive_intelligence_service import SCAN_SUMMARY_SCHEMA_VERSION

router = APIRouter(prefix="/intelligence", tags=["Intelligence"])


class ScanStatusResponse(BaseModel):
    scanned: bool
    completed_at: str | None = None
    watermark: int | None = None
    signal_count: int | None = None
    new_signal_count: int | None = None
    new_signal_ids: list[str] = []
    truncated: bool = False


@router.get(
    "/scan-status",
    response_model=ScanStatusResponse,
    summary="Latest proactive intelligence scan status",
    description=(
        "Returns the latest SUCCEEDED proactive scan for the caller's "
        "organisation, or scanned=false when no usable scan history exists. "
        "New-signal IDs are signal identifiers first observed by that scan."
    ),
)
def scan_status(
    ctx: Authorisation = Depends(require_permission(Permission.INTELLIGENCE_READ)),
) -> ScanStatusResponse:
    repository = ctx.repos.jobs if not ctx.anonymous else get_job_repository()
    candidates = [
        job
        for job in repository.list(
            status=BackgroundJobStatus.SUCCEEDED,
            organisation_id=ctx.organisation_id,
        )
        if job.job_type == BackgroundJobType.ORGANISATION_INTELLIGENCE_SCAN
        and job.completed_at is not None
    ]
    if not candidates:
        return ScanStatusResponse(scanned=False)
    latest = max(candidates, key=lambda job: job.completed_at)
    try:
        data = json.loads(latest.result_summary or "")
    except (ValueError, TypeError):
        return ScanStatusResponse(scanned=False)
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != SCAN_SUMMARY_SCHEMA_VERSION
    ):
        return ScanStatusResponse(scanned=False)
    try:
        watermark = int(data["watermark"])
        signal_count = int(data["signal_count"])
        new_ids = [str(item) for item in data.get("new_signal_ids", [])]
    except (KeyError, TypeError, ValueError):
        return ScanStatusResponse(scanned=False)
    return ScanStatusResponse(
        scanned=True,
        completed_at=latest.completed_at.isoformat() if latest.completed_at else None,
        watermark=watermark,
        signal_count=signal_count,
        new_signal_count=len(new_ids),
        new_signal_ids=new_ids,
        truncated=bool(data.get("truncated", False)),
    )
