"""Internal domain model for a Meeting.

This is the authoritative representation of a meeting inside Threadline.
It is *not* tied to any API schema or persistence format—those layers
translate to/from this model as needed.

Future pipeline stages (extraction, entity resolution, cross-meeting
correlation, etc.) will add fields here without forcing API changes.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Meeting(BaseModel):
    """Core domain representation of an ingested meeting."""

    # Identity
    meeting_id: str = Field(..., description="Unique meeting identifier (UUID).")

    # Content provided by the client
    title: str = Field(..., description="Human-readable meeting title.")
    transcript: str = Field(..., description="Full meeting transcript text.")
    meeting_date: datetime = Field(..., description="When the meeting took place.")
    participants: list[str] = Field(
        default_factory=list,
        description="Names of meeting participants.",
    )

    # Ingestion metadata – set by the service layer, never by the client
    ingested_at: datetime = Field(
        ..., description="UTC timestamp when this record was created in Threadline."
    )

    # ------------------------------------------------------------------
    # Extensibility note
    # ------------------------------------------------------------------
    # Future pipeline stages will add fields such as:
    #   extraction_result: Optional[ExtractionResult] = None
    #   entity_mentions: list[EntityMention] = []
    #   canonical_entities: list[CanonicalEntity] = []
    #   events: list[Event] = []
    #   threads: list[OrganizationalThread] = []
    # These are intentionally absent today.
