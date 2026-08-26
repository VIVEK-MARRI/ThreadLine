"""Internal domain models for extraction results.

These are the authoritative representations of extracted facts inside
Threadline.  They are *not* tied to any API schema or persistence format —
those layers translate to/from these models as needed.

Design notes
------------
- Evidence is a separate model so it can grow (speaker, timestamp, position)
  without changing the extracted-item models.
- All optional fields default to None; extraction code must never fabricate
  values for unknown fields.
- Future pipeline stages (entity resolution, cross-meeting correlation) will
  consume ExtractionResult without modifying this module.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

class Evidence(BaseModel):
    """A piece of transcript text that supports an extracted fact.

    Designed to grow: future versions will add meeting_id, speaker,
    timestamp, and transcript character offsets once those are available
    from the ingestion pipeline.
    """

    source_text: str = Field(
        ...,
        description=(
            "The verbatim or near-verbatim excerpt from the transcript "
            "that supports this extraction."
        ),
    )

    # Extensibility placeholders (not yet populated):
    # meeting_id: Optional[str] = None
    # speaker: Optional[str] = None
    # timestamp: Optional[datetime] = None
    # transcript_start: Optional[int] = None  # character offset
    # transcript_end: Optional[int] = None


# ---------------------------------------------------------------------------
# Extracted fact types
# ---------------------------------------------------------------------------

class Issue(BaseModel):
    """A problem, blocker, or concern explicitly mentioned in the meeting."""

    description: str = Field(
        ...,
        description="A concise description of the issue, faithful to the transcript.",
    )
    evidence: Evidence = Field(
        ...,
        description="Transcript text that supports this issue.",
    )


class Task(BaseModel):
    """An action item or commitment explicitly made in the meeting."""

    description: str = Field(
        ...,
        description="What needs to be done, as stated in the transcript.",
    )
    evidence: Evidence = Field(
        ...,
        description="Transcript text that supports this task.",
    )
    owner: Optional[str] = Field(
        default=None,
        description=(
            "Person responsible, if explicitly named.  "
            "None if not mentioned — never guessed."
        ),
    )
    deadline: Optional[str] = Field(
        default=None,
        description=(
            "Deadline as stated in the transcript (e.g. 'Friday', 'end of Q3').  "
            "None if not mentioned — never inferred."
        ),
    )


class Decision(BaseModel):
    """A conclusion or agreement explicitly reached during the meeting."""

    description: str = Field(
        ...,
        description=(
            "The decision as stated.  Conditional language must be preserved "
            "(e.g. 'if the issue is not resolved')."
        ),
    )
    evidence: Evidence = Field(
        ...,
        description="Transcript text that supports this decision.",
    )


class Risk(BaseModel):
    """A risk or concern explicitly raised in the meeting."""

    description: str = Field(
        ...,
        description="The risk as described in the transcript.",
    )
    evidence: Evidence = Field(
        ...,
        description="Transcript text that supports this risk.",
    )
    severity: Optional[str] = Field(
        default=None,
        description=(
            "Severity level if explicitly stated (e.g. 'high', 'critical').  "
            "None if not mentioned — never assessed by the extraction layer."
        ),
    )


# ---------------------------------------------------------------------------
# Top-level result
# ---------------------------------------------------------------------------

class ExtractionResult(BaseModel):
    """The complete set of facts extracted from a single meeting transcript."""

    meeting_id: str = Field(
        ..., description="ID of the meeting this result was extracted from."
    )
    extracted_at: datetime = Field(
        ..., description="UTC timestamp when this extraction was produced."
    )
    issues: list[Issue] = Field(
        default_factory=list,
        description="Problems or blockers explicitly mentioned.",
    )
    tasks: list[Task] = Field(
        default_factory=list,
        description="Action items or commitments explicitly made.",
    )
    decisions: list[Decision] = Field(
        default_factory=list,
        description="Conclusions or agreements explicitly reached.",
    )
    risks: list[Risk] = Field(
        default_factory=list,
        description="Risks or concerns explicitly raised.",
    )
