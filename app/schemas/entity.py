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

    Mirrors the domain model's ResolutionStatus enum.
    Kept as an independent enum so the API surface can evolve separately
    from the internal model if needed.
    """

    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


class ResolutionOutcomeSchema(str, Enum):
    """Resolution outcome values returned by the Resolution Decision endpoint.

    The outcome is the decision engine's verdict for a specific invocation.
    It is separate from ResolutionStatusSchema (the stored mention state)
    to allow the two to diverge cleanly in future API versions.

    RESOLVED   — the engine selected a canonical entity for this mention.
    AMBIGUOUS  — the top candidate exceeded the threshold but was too close
                 to the second candidate; no entity was selected.
    UNRESOLVED — no candidate met the confidence threshold; no entity selected.
    """

    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
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



# ---------------------------------------------------------------------------
# Candidate scoring schemas
# ---------------------------------------------------------------------------

class ScoredEntityCandidateSchema(BaseModel):
    """A single candidate entity with its lexical similarity score.

    Returned by the scored-candidates endpoint.  Every field is included
    to keep the API evidence-backed and auditable.

    score is always in [0.0, 1.0]:
      - 1.0 indicates an exact normalised match.
      - Lower values indicate partial lexical overlap.

    Component scores (mention_coverage, candidate_coverage) explain how
    the final score was derived.

    matched_representation identifies which canonical name or alias of the
    entity produced the best score.
    """

    entity_id: str = Field(..., description="Unique identifier of the candidate entity.")
    canonical_name: str = Field(..., description="Preferred name of the candidate entity.")
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Aggregated similarity score in [0.0, 1.0].  "
            "1.0 = exact normalised match; lower = partial overlap."
        ),
    )
    scoring_method: str = Field(
        ...,
        description="Identifier of the scoring algorithm (e.g. 'lexical_weighted_coverage').",
    )
    matched_representation: str = Field(
        ...,
        description="The canonical name or alias that produced the best score.",
    )
    mention_coverage: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of mention tokens covered by the overlap "
            "(overlap / mention_token_count)."
        ),
    )
    candidate_coverage: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of matched-representation tokens covered by the overlap "
            "(overlap / representation_token_count)."
        ),
    )
    exact_match: bool = Field(
        ...,
        description=(
            "True when the normalised mention equals the candidate's "
            "canonical name or an alias exactly.  Always yields score=1.0."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "entity_id": "entity_001",
                    "canonical_name": "rahul kumar",
                    "score": 1.0,
                    "scoring_method": "lexical_weighted_coverage",
                    "matched_representation": "rahul kumar",
                    "mention_coverage": 1.0,
                    "candidate_coverage": 1.0,
                    "exact_match": True,
                },
                {
                    "entity_id": "entity_002",
                    "canonical_name": "rahul sharma",
                    "score": 0.6,
                    "scoring_method": "lexical_weighted_coverage",
                    "matched_representation": "rahul sharma",
                    "mention_coverage": 1.0,
                    "candidate_coverage": 0.5,
                    "exact_match": False,
                },
            ]
        }
    }


class ScoredCandidatesResponse(BaseModel):
    """Response body for GET /entities/mentions/{mention_id}/scored-candidates.

    Always returns HTTP 200 when the mention exists, regardless of resolution
    status.  When the mention is already RESOLVED, candidates is an empty list
    -- the mention has a confirmed identity and scoring is skipped.

    This endpoint is read-only and never modifies the mention.
    """

    mention_id: str = Field(
        ..., description="ID of the mention candidates were scored for."
    )
    resolution_status: ResolutionStatusSchema = Field(
        ...,
        description="Current resolution status of the mention.",
    )
    candidates: list[ScoredEntityCandidateSchema] = Field(
        default_factory=list,
        description=(
            "Scored, ordered list of candidate canonical entities.  "
            "Ordered by score descending, then canonical_name ascending, "
            "then entity_id ascending.  "
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
                            "canonical_name": "rahul kumar",
                            "score": 1.0,
                            "scoring_method": "lexical_weighted_coverage",
                            "matched_representation": "rahul kumar",
                            "mention_coverage": 1.0,
                            "candidate_coverage": 1.0,
                            "exact_match": True,
                        }
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


# ---------------------------------------------------------------------------
# Resolution Decision schemas
# ---------------------------------------------------------------------------

class ResolutionDecisionResponse(BaseModel):
    """Response body for POST /entities/mentions/{mention_id}/resolve.

    Records the decision made by the Resolution Decision Engine together with
    enough evidence to explain why the decision was reached.

    Important: top_score and second_score are lexical similarity scores in
    [0.0, 1.0].  They are NOT probabilities.  A score of 0.92 means the
    candidate received a lexical similarity score of 0.92 under the scoring
    function — NOT that there is a 92 % chance the entity is correct.
    """

    mention_id: str = Field(
        ..., description="ID of the mention the decision was made for."
    )
    outcome: ResolutionOutcomeSchema = Field(
        ...,
        description=(
            "The engine's decision: RESOLVED, AMBIGUOUS, or UNRESOLVED.  "
            "RESOLVED means the mention was matched to a canonical entity.  "
            "AMBIGUOUS means candidates were found but no clear winner existed.  "
            "UNRESOLVED means no candidate met the confidence threshold."
        ),
    )
    selected_entity_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of the canonical entity selected when outcome is RESOLVED.  "
            "Always null for AMBIGUOUS and UNRESOLVED decisions."
        ),
    )
    top_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Lexical similarity score of the top-ranked candidate in [0.0, 1.0].  "
            "This is NOT a probability — it is the raw output of the scoring function.  "
            "Null when there were no candidates."
        ),
    )
    second_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Lexical similarity score of the second-ranked candidate in [0.0, 1.0].  "
            "Null when there were fewer than two candidates."
        ),
    )
    score_margin: Optional[float] = Field(
        default=None,
        description=(
            "Difference between top_score and second_score (top − second).  "
            "Null when second_score is null.  "
            "A larger margin indicates a more clearly dominant top candidate."
        ),
    )
    reason: str = Field(
        ...,
        description="Human-readable explanation of why this decision was made.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "mention_id": "m_001",
                    "outcome": "RESOLVED",
                    "selected_entity_id": "entity_001",
                    "top_score": 0.94,
                    "second_score": 0.40,
                    "score_margin": 0.54,
                    "reason": (
                        "Top candidate exceeded the confidence threshold "
                        "(0.9400 >= 0.8500) and had sufficient margin over the "
                        "second candidate (margin 0.5400 >= 0.1000)."
                    ),
                },
                {
                    "mention_id": "m_002",
                    "outcome": "AMBIGUOUS",
                    "selected_entity_id": None,
                    "top_score": 0.91,
                    "second_score": 0.90,
                    "score_margin": 0.01,
                    "reason": (
                        "Top candidate exceeded the confidence threshold but "
                        "was too close to the second candidate."
                    ),
                },
                {
                    "mention_id": "m_003",
                    "outcome": "UNRESOLVED",
                    "selected_entity_id": None,
                    "top_score": 0.55,
                    "second_score": None,
                    "score_margin": None,
                    "reason": "No candidate exceeded the confidence threshold.",
                },
            ]
        }
    }
