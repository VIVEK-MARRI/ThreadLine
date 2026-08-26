"""Pydantic schemas for the Extraction API.

These are the *public contract* of the extraction endpoint — what clients
receive.  They mirror the internal domain models closely today but are kept
separate so the API surface can remain stable while domain models evolve.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Evidence (public representation)
# ---------------------------------------------------------------------------

class EvidenceSchema(BaseModel):
    """Supporting transcript text for an extracted fact."""

    source_text: str = Field(
        ...,
        description="Verbatim or near-verbatim excerpt from the transcript.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"source_text": "Rahul reported that the payment provider API is still unstable."}
            ]
        }
    }


# ---------------------------------------------------------------------------
# Extracted item schemas
# ---------------------------------------------------------------------------

class IssueSchema(BaseModel):
    description: str
    evidence: EvidenceSchema


class TaskSchema(BaseModel):
    description: str
    evidence: EvidenceSchema
    owner: Optional[str] = None
    deadline: Optional[str] = None


class DecisionSchema(BaseModel):
    description: str
    evidence: EvidenceSchema


class RiskSchema(BaseModel):
    description: str
    evidence: EvidenceSchema
    severity: Optional[str] = None


# ---------------------------------------------------------------------------
# Top-level response
# ---------------------------------------------------------------------------

class ExtractionResponse(BaseModel):
    """Response body for POST /api/v1/meetings/{meeting_id}/extract."""

    meeting_id: str = Field(..., description="ID of the source meeting.")
    extracted_at: datetime = Field(..., description="UTC timestamp of extraction.")
    issues: list[IssueSchema] = Field(default_factory=list)
    tasks: list[TaskSchema] = Field(default_factory=list)
    decisions: list[DecisionSchema] = Field(default_factory=list)
    risks: list[RiskSchema] = Field(default_factory=list)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "meeting_id": "a1b2c3d4-...",
                    "extracted_at": "2026-08-23T22:05:00Z",
                    "issues": [
                        {
                            "description": "Payment provider API is unstable.",
                            "evidence": {
                                "source_text": "Rahul reported that the payment provider API is still unstable."
                            },
                        }
                    ],
                    "tasks": [
                        {
                            "description": "Investigate the payment provider issue.",
                            "owner": "Rahul",
                            "deadline": "Friday",
                            "evidence": {
                                "source_text": "Priya asked him to investigate the issue before Friday."
                            },
                        }
                    ],
                    "decisions": [
                        {
                            "description": "Delay the release if the issue is not resolved.",
                            "evidence": {
                                "source_text": "The team agreed to delay the release if the issue is not resolved."
                            },
                        }
                    ],
                    "risks": [],
                }
            ]
        }
    }
