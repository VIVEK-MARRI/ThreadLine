"""Pydantic schemas for the Meetings API.

These are the *public contract* of the API — what clients send and receive.
They are deliberately kept separate from the internal Meeting domain model
so the API surface can remain stable while the internal model evolves.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.entity import EntityTypeSchema, ResolutionStatusSchema
from app.schemas.extraction import ExtractionResponse


class MeetingIngestRequest(BaseModel):
    """Request body for POST /api/v1/meetings."""

    title: str = Field(..., description="Meeting title.")
    transcript: str = Field(..., description="Full meeting transcript.")
    meeting_date: datetime = Field(..., description="ISO-8601 datetime of the meeting.")
    participants: Optional[list[str]] = Field(
        default=None,
        description="Optional list of participant names.",
    )
    meeting_id: Optional[str] = Field(
        default=None,
        description="Optional stable ID for idempotent re-ingestion.",
    )

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be blank")
        return v.strip()

    @field_validator("transcript")
    @classmethod
    def transcript_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("transcript must not be blank")
        return v.strip()

    @field_validator("participants")
    @classmethod
    def participants_must_be_non_empty_strings(
        cls, v: Optional[list[str]]
    ) -> Optional[list[str]]:
        if v is None:
            return v
        for name in v:
            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    "each participant name must be a non-empty string"
                )
        return [name.strip() for name in v]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "title": "Payment Integration Weekly Sync",
                    "transcript": (
                        "Rahul reported that the payment provider API is still "
                        "unstable. Priya asked him to investigate the issue before Friday."
                    ),
                    "meeting_date": "2026-08-23T10:00:00Z",
                    "participants": ["Rahul Kumar", "Priya Sharma"],
                }
            ]
        }
    }


class MeetingIngestResponse(BaseModel):
    """Response body for POST /api/v1/meetings."""

    meeting_id: str = Field(..., description="Unique identifier of the ingested meeting.")
    status: str = Field(..., description="Ingestion status.")

    model_config = {
        "json_schema_extra": {
            "examples": [{"meeting_id": "a1b2c3d4-...", "status": "ingested"}]
        }
    }


class MeetingResponse(BaseModel):
    """Response body for GET /api/v1/meetings/{meeting_id}."""

    meeting_id: str
    title: str
    transcript: str
    meeting_date: datetime
    participants: list[str]
    ingested_at: datetime

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "meeting_id": "a1b2c3d4-...",
                    "title": "Payment Integration Weekly Sync",
                    "transcript": "Rahul reported that the payment provider API is still unstable.",
                    "meeting_date": "2026-08-23T10:00:00Z",
                    "participants": ["Rahul Kumar", "Priya Sharma"],
                    "ingested_at": "2026-08-23T22:00:00Z",
                }
            ]
        }
    }


class HealthResponse(BaseModel):
    """Response body for GET /health."""

    status: str


class MeetingProcessingStatusSchema(str, Enum):
    """Processing lifecycle values returned for a meeting.

    Mirrors the durable status vocabulary produced by
    ``get_consistency_status``: CURRENT means source, succeeded processing,
    extraction, and semantic evidence agree; PENDING means work for the
    current source is queued, running, or awaiting retry; FAILED means the
    current source's newest relevant job failed; STALE means succeeded work
    exists but is older than the current source; INCOMPLETE means no
    succeeded, failed, or pending work is recorded for the current source.
    """

    CURRENT = "CURRENT"
    PENDING = "PENDING"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"
    STALE = "STALE"


class MeetingSummarySchema(BaseModel):
    """A bounded list-view record for one meeting.

    The full transcript is intentionally omitted: lists stay bounded while
    meeting detail remains the authoritative source record.
    """

    meeting_id: str = Field(..., description="Unique identifier of the meeting.")
    title: str = Field(..., description="Human-readable meeting title.")
    meeting_date: datetime = Field(..., description="ISO-8601 datetime of the meeting.")
    participants: list[str] = Field(
        default_factory=list,
        description="Participant names stored with the meeting.",
    )
    ingested_at: datetime = Field(
        ..., description="UTC timestamp when this record was created in Threadline."
    )
    source_revision: int = Field(
        ..., ge=1, description="Monotonic authoritative revision of the meeting source."
    )
    processing_status: MeetingProcessingStatusSchema = Field(
        ..., description="Durable processing lifecycle status for the current source."
    )
    extraction_revision: Optional[int] = Field(
        default=None,
        description="Source revision used by the stored extraction, when present.",
    )
    extracted_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp of the stored extraction, when present.",
    )
    issue_count: int = Field(default=0, ge=0, description="Stored extracted issues.")
    task_count: int = Field(default=0, ge=0, description="Stored extracted tasks.")
    decision_count: int = Field(default=0, ge=0, description="Stored extracted decisions.")
    risk_count: int = Field(default=0, ge=0, description="Stored extracted risks.")
    mention_count: int = Field(
        default=0,
        ge=0,
        description="Current-revision mentions observed in this meeting.",
    )
    resolved_entity_count: int = Field(
        default=0,
        ge=0,
        description="Distinct entities resolved from current-revision mentions.",
    )


class MeetingListResponse(BaseModel):
    """Response body for GET /api/v1/meetings."""

    meetings: list[MeetingSummarySchema] = Field(
        default_factory=list,
        description="Tenant-scoped meetings, newest meeting_date first.",
    )
    limit: int = Field(..., ge=1, description="Maximum meetings requested.")
    returned_count: int = Field(
        ..., ge=0, description="Number of meeting summaries actually returned."
    )
    has_more: bool = Field(
        ..., description="True when more tenant meetings exist beyond this page."
    )


class MeetingExtractionResponse(BaseModel):
    """Response body for GET /api/v1/meetings/{meeting_id}/extraction."""

    meeting_id: str = Field(..., description="ID of the source meeting.")
    has_extraction: bool = Field(
        ..., description="True when a stored extraction result exists."
    )
    extraction: Optional[ExtractionResponse] = Field(
        default=None,
        description="Stored extraction result, or null before extraction succeeds.",
    )


class MeetingProcessingResponse(BaseModel):
    """Response body for GET /api/v1/meetings/{meeting_id}/processing."""

    meeting_id: str = Field(..., description="ID of the source meeting.")
    source_revision: int = Field(
        ..., ge=1, description="Monotonic authoritative revision of the meeting source."
    )
    status: MeetingProcessingStatusSchema = Field(
        ..., description="Durable processing lifecycle status for the current source."
    )
    processing_complete: bool = Field(
        ..., description="True when current source and derived processing agree."
    )
    is_current: bool = Field(
        ...,
        description="True when source, succeeded processing, extraction, and semantic evidence agree.",
    )
    extraction_revision: Optional[int] = Field(
        default=None,
        description="Source revision used by the stored extraction, when present.",
    )
    derived_revision: Optional[int] = Field(
        default=None,
        description="Highest succeeded processing source revision, when present.",
    )
    semantic_revision: Optional[int] = Field(
        default=None,
        description="Common semantic-evidence source revision, when agreed.",
    )
    stale_mentions: int = Field(
        default=0,
        ge=0,
        description="Mentions stamped with a non-current source revision.",
    )
    worker_enabled: bool = Field(
        ..., description="Whether the background worker is enabled server-side."
    )


class MeetingMentionSchema(BaseModel):
    """A stored entity mention observed in one meeting."""

    mention_id: str = Field(..., description="Unique mention identifier.")
    meeting_id: str = Field(..., description="ID of the meeting where it was observed.")
    entity_type: EntityTypeSchema = Field(
        ..., description="Entity category this mention is believed to refer to."
    )
    text: str = Field(..., description="The exact text as it appeared in the transcript.")
    source_text: str = Field(
        ..., description="The surrounding transcript excerpt that contains this mention."
    )
    entity_id: Optional[str] = Field(
        default=None,
        description="ID of the resolved canonical entity, or null if unresolved.",
    )
    resolution_status: ResolutionStatusSchema = Field(
        ..., description="Stored resolution state for this mention."
    )
    source_revision: int = Field(
        ..., ge=1, description="Authoritative source revision used for this mention."
    )


class MeetingMentionsResponse(BaseModel):
    """Response body for GET /api/v1/meetings/{meeting_id}/mentions."""

    meeting_id: str = Field(..., description="ID of the source meeting.")
    mention_count: int = Field(
        ..., ge=0, description="Current-revision mentions observed in this meeting."
    )
    resolved_mention_count: int = Field(
        ..., ge=0, description="Current-revision mentions resolved to an entity."
    )
    mentions: list[MeetingMentionSchema] = Field(
        default_factory=list,
        description="Current-revision mentions in stored order.",
    )
