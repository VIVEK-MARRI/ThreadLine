"""Pydantic schemas for the Meetings API.

These are the *public contract* of the API — what clients send and receive.
They are deliberately kept separate from the internal Meeting domain model
so the API surface can remain stable while the internal model evolves.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class MeetingIngestRequest(BaseModel):
    """Request body for POST /api/v1/meetings."""

    title: str = Field(..., description="Meeting title.")
    transcript: str = Field(..., description="Full meeting transcript.")
    meeting_date: datetime = Field(..., description="ISO-8601 datetime of the meeting.")
    participants: Optional[list[str]] = Field(
        default=None,
        description="Optional list of participant names.",
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
