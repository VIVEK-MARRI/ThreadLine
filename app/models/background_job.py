"""Durable-independent in-process background job models."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class BackgroundJobType(str, Enum):
    MEETING_PROCESSING = "MEETING_PROCESSING"
    DERIVED_INTELLIGENCE_REBUILD = "DERIVED_INTELLIGENCE_REBUILD"
    SEMANTIC_INDEXING = "SEMANTIC_INDEXING"


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
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    next_retry_at: Optional[datetime] = None
    lease_until: Optional[datetime] = None
    worker_id: Optional[str] = None
    stage: Optional[str] = None
    error_type: Optional[str] = None
    last_error: Optional[str] = None
    attempts: int = Field(default=0, ge=0)

    @property
    def error(self) -> Optional[str]:
        """Compatibility view for the Stage 22 in-process service."""
        return self.last_error
