"""Pydantic schemas for the Entities API.

These are the *public contract* of the entities endpoints — what clients send
and receive.  They are deliberately kept separate from the internal domain
models so the API surface can remain stable while the internal models evolve.

EntityTypeSchema and ResolutionStatusSchema mirror their domain counterparts
today but are independent so the two layers can diverge in the future without
forcing a breaking API change.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Controlled vocabularies (API layer copies)
# ---------------------------------------------------------------------------

class EntityTypeSchema(str, Enum):
    """Entity type values accepted and returned by the API."""

    PERSON = "PERSON"
    ISSUE = "ISSUE"


class ResolutionStatusSchema(str, Enum):
    """Resolution status values returned by the API.

    Kept as an enum so future states (PENDING_REVIEW, AMBIGUOUS, REJECTED)
    can be added without changing the API contract.
    """

    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"


# ---------------------------------------------------------------------------
# Entity request / response schemas
# ---------------------------------------------------------------------------

class CreateEntityRequest(BaseModel):
    """Request body for POST /api/v1/entities."""

    entity_type: EntityTypeSchema = Field(
        ..., description="The category of this entity (e.g. PERSON, ISSUE)."
    )
    canonical_name: str = Field(
        ..., description="The preferred name for this entity."
    )

    @field_validator("canonical_name")
    @classmethod
    def canonical_name_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("canonical_name must not be blank")
        return v

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"entity_type": "PERSON", "canonical_name": "Rahul Kumar"},
                {"entity_type": "ISSUE", "canonical_name": "Payment API Instability"},
            ]
        }
    }


class EntityResponse(BaseModel):
    """Response body for entity endpoints."""

    entity_id: str = Field(..., description="Unique entity identifier.")
    entity_type: EntityTypeSchema = Field(..., description="Category of this entity.")
    canonical_name: str = Field(..., description="Preferred name for this entity.")
    aliases: list[str] = Field(
        default_factory=list,
        description="Alternative names that resolve to this entity.",
    )
    created_at: datetime = Field(
        ..., description="UTC timestamp when this entity was created."
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "entity_id": "entity_001",
                    "entity_type": "PERSON",
                    "canonical_name": "Rahul Kumar",
                    "aliases": ["Rahul", "R. Kumar"],
                    "created_at": "2026-08-23T22:00:00Z",
                }
            ]
        }
    }


# ---------------------------------------------------------------------------
# Mention request / response schemas
# ---------------------------------------------------------------------------

class RegisterMentionRequest(BaseModel):
    """Request body for POST /api/v1/entities/mentions."""

    entity_type: EntityTypeSchema = Field(
        ..., description="The entity category this mention is believed to refer to."
    )
    text: str = Field(
        ..., description="The surface form as it appeared in the transcript."
    )
    meeting_id: str = Field(
        ..., description="ID of the meeting where this mention was observed."
    )
    source_text: str = Field(
        ...,
        description="The surrounding transcript excerpt that contains this mention.",
    )

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be blank")
        return v

    @field_validator("meeting_id")
    @classmethod
    def meeting_id_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("meeting_id must not be blank")
        return v

    @field_validator("source_text")
    @classmethod
    def source_text_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("source_text must not be blank")
        return v

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "entity_type": "PERSON",
                    "text": "Rahul Kumar",
                    "meeting_id": "meeting_123",
                    "source_text": "Rahul Kumar reported the API issue.",
                }
            ]
        }
    }


class RegisterMentionResponse(BaseModel):
    """Response body for POST /api/v1/entities/mentions.

    Clearly communicates whether the mention was resolved and, if so,
    which canonical entity it maps to.
    """

    mention_id: str = Field(..., description="Unique mention identifier.")
    text: str = Field(..., description="The surface form as registered.")
    entity_type: EntityTypeSchema = Field(
        ..., description="Entity category of this mention."
    )
    entity_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of the resolved canonical entity, or null if unresolved."
        ),
    )
    resolution_status: ResolutionStatusSchema = Field(
        ...,
        description=(
            "RESOLVED if matched to a canonical entity; "
            "UNRESOLVED if no safe match was found."
        ),
    )
    created_at: datetime = Field(
        ..., description="UTC timestamp when this mention was registered."
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "mention_id": "m_001",
                    "text": "Rahul Kumar",
                    "entity_type": "PERSON",
                    "entity_id": "entity_001",
                    "resolution_status": "RESOLVED",
                    "created_at": "2026-08-23T22:05:00Z",
                },
                {
                    "mention_id": "m_002",
                    "text": "the backend lead",
                    "entity_type": "PERSON",
                    "entity_id": None,
                    "resolution_status": "UNRESOLVED",
                    "created_at": "2026-08-23T22:05:01Z",
                },
            ]
        }
    }


# ---------------------------------------------------------------------------
# Candidate generation schemas
# ---------------------------------------------------------------------------

class EntityCandidateSchema(BaseModel):
    """A single candidate entity returned by the candidate generation endpoint.

    A candidate is NOT a resolution decision.  It represents one entity that
    is plausible given the mention's surface form and should be evaluated in
    a future scoring stage.

    candidate_reason identifies which strategy produced this candidate
    (e.g. "lexical_token_overlap").  There is intentionally no confidence
    score — scoring is a future pipeline stage, not part of candidate
    generation.
    """

    entity_id: str = Field(..., description="Unique identifier of the candidate entity.")
    entity_type: EntityTypeSchema = Field(
        ..., description="Category of the candidate entity."
    )
    canonical_name: str = Field(
        ..., description="Preferred name of the candidate entity."
    )
    candidate_reason: str = Field(
        ...,
        description=(
            "Human-readable label explaining why this entity was selected "
            "as a candidate (e.g. 'lexical_token_overlap')."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "entity_id": "entity_001",
                    "entity_type": "PERSON",
                    "canonical_name": "rahul kumar",
                    "candidate_reason": "lexical_token_overlap",
                }
            ]
        }
    }


class CandidatesResponse(BaseModel):
    """Response body for GET /entities/mentions/{mention_id}/candidates.

    Always returns HTTP 200 when the mention exists, regardless of resolution
    status.  When the mention is already RESOLVED, candidates is an empty list
    — the mention has a confirmed identity and candidate generation is skipped.
    """

    mention_id: str = Field(..., description="ID of the mention candidates were generated for.")
    resolution_status: ResolutionStatusSchema = Field(
        ...,
        description="Current resolution status of the mention.",
    )
    candidates: list[EntityCandidateSchema] = Field(
        default_factory=list,
        description=(
            "Ordered list of candidate canonical entities.  "
            "Empty when the mention is already RESOLVED or no entity has "
            "meaningful lexical overlap with the mention text."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "mention_id": "m_001",
                    "resolution_status": "UNRESOLVED",
                    "candidates": [
                        {
                            "entity_id": "entity_001",
                            "entity_type": "PERSON",
                            "canonical_name": "rahul kumar",
                            "candidate_reason": "lexical_token_overlap",
                        },
                        {
                            "entity_id": "entity_002",
                            "entity_type": "PERSON",
                            "canonical_name": "rahul sharma",
                            "candidate_reason": "lexical_token_overlap",
                        },
                    ],
                },
                {
                    "mention_id": "m_002",
                    "resolution_status": "RESOLVED",
                    "candidates": [],
                },
            ]
        }
    }

