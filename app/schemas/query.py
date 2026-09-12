"""Pydantic schemas for the Natural Language Query API (Stage 18).

These schemas define the external HTTP contract. They are mapped to/from
the internal domain models by the API router.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.natural_language import (
    DEFAULT_MAX_EVIDENCE_ITEMS,
    MAX_EVIDENCE_ITEMS,
    EvidenceItem,
    EvidenceType,
    QueryIntent,
)


class NaturalLanguageQueryRequest(BaseModel):
    """Request schema for submitting a natural language query."""

    question: str = Field(
        ...,
        min_length=1,
        description="The natural-language question.",
        examples=["What is blocking Project Atlas?"],
    )

    entity_id: Optional[str] = Field(
        default=None,
        description="Optional pre-resolved entity ID. If omitted, ThreadLine attempts to extract it.",
    )

    max_evidence_items: int = Field(
        default=DEFAULT_MAX_EVIDENCE_ITEMS,
        ge=1,
        le=MAX_EVIDENCE_ITEMS,
        description="Maximum number of evidence items to retrieve.",
    )

    include_source_text: bool = Field(
        default=True,
        description="Whether to include verbatim transcript excerpts in evidence.",
    )


class EvidenceItemSchema(BaseModel):
    """Schema for a retrieved evidence item in API responses."""

    evidence_id: str
    evidence_type: EvidenceType
    entity_id: Optional[str] = None
    meeting_id: Optional[str] = None
    mention_id: Optional[str] = None
    source_text: Optional[str] = None
    timestamp: Optional[datetime] = None
    summary: str
    severity_weight: int
    metadata: dict
    source_reference: Optional[str] = None


class NaturalLanguageQueryResponse(BaseModel):
    """Response schema for a grounded natural language answer."""

    query_id: str
    question: str
    intent: QueryIntent
    entity_id: Optional[str] = None
    answer: str
    evidence: list[EvidenceItemSchema]
    cited_evidence_ids: list[str]
    insufficient_evidence: bool
    warnings: list[str]
    generated_at: Optional[datetime] = None


class NaturalLanguageEvidenceResponse(BaseModel):
    """Response schema for the debug/transparency evidence endpoint."""

    query_id: str
    question: str
    intent: QueryIntent
    entity_id: Optional[str] = None
    evidence: list[EvidenceItemSchema]
