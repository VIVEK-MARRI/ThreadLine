"""Durable-independent in-process background job models."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.auth.constants import DEFAULT_ORGANISATION_ID


class BackgroundJobType(str, Enum):
    MEETING_PROCESSING = "MEETING_PROCESSING"
    DERIVED_INTELLIGENCE_REBUILD = "DERIVED_INTELLIGENCE_REBUILD"
    SEMANTIC_INDEXING = "SEMANTIC_INDEXING"
    ORGANISATION_INTELLIGENCE_SCAN = "ORGANISATION_INTELLIGENCE_SCAN"


class BackgroundJobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    COMPLETED = "SUCCEEDED"  # Backward-compatible alias.
    RETRY_WAITING = "RETRY_WAITING"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class BackgroundJob(BaseModel):
    job_id: str
    job_type: BackgroundJobType
    status: BackgroundJobStatus = BackgroundJobStatus.PENDING
    created_at: datetime
    payload_id: str = ""
    max_attempts: int = Field(default=3, ge=1)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    next_retry_at: datetime | None = None
    lease_until: datetime | None = None
    worker_id: str | None = None
    stage: str | None = None
    error_type: str | None = None
    last_error: str | None = None
    attempts: int = Field(default=0, ge=0)
    processing_revision: str | None = None
    result_summary: str | None = Field(
        default=None,
        description=(
            "Opaque durable result payload (Stage 34): JSON scan summary for "
            "ORGANISATION_INTELLIGENCE_SCAN jobs (observed source watermark, "
            "deterministic signal IDs, new-signal IDs, completion time). "
            "Written by the scan handler before success; read by the "
            "scan-status endpoint and by later scans for new-signal diffing. "
            "None for all other job types."
        ),
    )
    organisation_id: str = Field(
        default=DEFAULT_ORGANISATION_ID,
        description=(
            "Tenant scope: the organisation whose data this job processes. "
            "Stamped at enqueue from the meeting; preserved across retry/recovery. "
            "The worker derives its entire tenant context from this field."
        ),
    )

    @property
    def error(self) -> str | None:
        """Compatibility view for the Stage 22 in-process service."""
        return self.last_error
