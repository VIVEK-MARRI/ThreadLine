"""Internal domain models for the Entity Resolution system.

These are the authoritative representations of canonical entities and entity
mentions inside Threadline.  They are *not* tied to any API schema or
persistence format — those layers translate to/from these models as needed.

Design notes
------------
- EntityType and ResolutionStatus are StrEnums so Pydantic v2 serialises them
  as plain strings (e.g., "PERSON") without any extra model config.
- CanonicalEntity is intentionally separate from EntityMention.
  An entity is an organisational object; a mention is an observed reference.
- entity_id on EntityMention is Optional to support unresolved mentions.
- ResolutionStatus is an enum (not bool) so future states like PENDING_REVIEW
  or AMBIGUOUS can be added without changing the API contract.

Future pipeline stages (fuzzy resolution, embeddings, cross-meeting correlation)
will operate on these models without modifying this module.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

class EntityType(str, Enum):
    """The category of real-world object a canonical entity represents.

    Deliberately small for today's foundation.  Add new values here
    (e.g. TEAM, PROJECT, SYSTEM, INITIATIVE) as the system matures.
    """

    PERSON = "PERSON"
    ISSUE = "ISSUE"


class ResolutionStatus(str, Enum):
    """Whether a mention has been matched to a canonical entity.

    RESOLVED  — the mention was matched to a known canonical entity.
    UNRESOLVED — no safe match was found; the mention is stored as-is.

    Future states (not implemented today):
        PENDING_REVIEW — a candidate match exists but requires human confirmation.
        AMBIGUOUS      — multiple plausible matches were found.
        REJECTED       — a proposed match was explicitly declined.
    """

    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"


# ---------------------------------------------------------------------------
# Canonical Entity
# ---------------------------------------------------------------------------

class CanonicalEntity(BaseModel):
    """The authoritative record of a real-world organisational object.

    A canonical entity is the single source of truth for a person, issue,
    or other object that Threadline tracks across meetings.  Multiple
    EntityMentions (observed surface forms) may resolve to one entity.

    Example
    -------
    id: "entity_001"
    entity_type: PERSON
    canonical_name: "Rahul Kumar"
    aliases: ["Rahul", "R. Kumar"]
    """

    # Identity
    entity_id: str = Field(..., description="Unique entity identifier (UUID).")

    # Classification
    entity_type: EntityType = Field(..., description="Category of this entity.")

    # Naming
    canonical_name: str = Field(
        ...,
        description=(
            "The preferred, normalised name for this entity.  "
            "Used as the primary key for exact-match resolution."
        ),
    )
    aliases: list[str] = Field(
        default_factory=list,
        description=(
            "Alternative names or surface forms that resolve to this entity.  "
            "Adding an alias does NOT automatically merge other entities."
        ),
    )

    # Metadata – set by the service layer, never by the client
    created_at: datetime = Field(
        ..., description="UTC timestamp when this entity was first created."
    )


# ---------------------------------------------------------------------------
# Entity Mention
# ---------------------------------------------------------------------------

class EntityMention(BaseModel):
    """An observed reference to an entity within a specific meeting transcript.

    A mention captures *how* something was referred to (the surface form)
    and *where* it appeared (meeting + supporting text).  It may or may not
    resolve to a canonical entity.

    Example (resolved)
    ------------------
    text: "Rahul"
    meeting_id: "meeting_123"
    source_text: "Rahul reported that the payment API is unstable."
    entity_id: "entity_001"
    resolution_status: RESOLVED

    Example (unresolved)
    --------------------
    text: "the backend lead"
    meeting_id: "meeting_123"
    source_text: "The backend lead will own this."
    entity_id: None
    resolution_status: UNRESOLVED
    """

    # Identity
    mention_id: str = Field(..., description="Unique mention identifier (UUID).")

    # Classification (denormalised for efficient querying without joining entities)
    entity_type: EntityType = Field(
        ..., description="Entity category this mention is believed to refer to."
    )

    # Surface form
    text: str = Field(
        ..., description="The exact text as it appeared in the transcript."
    )

    # Source context
    meeting_id: str = Field(
        ..., description="ID of the meeting where this mention was observed."
    )
    source_text: str = Field(
        ...,
        description=(
            "The surrounding transcript excerpt that contains this mention.  "
            "Provides evidence for future human review or automated resolution."
        ),
    )

    # Resolution — Optional: None means unresolved
    entity_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of the resolved canonical entity, or None if unresolved.  "
            "Absence of a value is meaningful and must never be fabricated."
        ),
    )
    resolution_status: ResolutionStatus = Field(
        default=ResolutionStatus.UNRESOLVED,
        description="Whether this mention has been matched to a canonical entity.",
    )

    # Metadata – set by the service layer
    created_at: datetime = Field(
        ..., description="UTC timestamp when this mention was registered."
    )


# ---------------------------------------------------------------------------
# Entity Candidate
# ---------------------------------------------------------------------------

class EntityCandidate(BaseModel):
    """A plausible canonical entity suggested for an unresolved mention.

    A candidate is NOT a resolution decision.  It is the output of
    candidate generation — a ranked shortlist of entities worth evaluating
    in the next pipeline stage (future: candidate scoring).

    Design notes
    ------------
    - candidate_reason is a plain str (not an enum) so future generators
      (embedding-based, contextual) can use their own reason labels without
      a schema migration.
    - There is intentionally no confidence_score field.  A candidate is
      triage output, not a prediction.  Scores belong in the scoring stage.
    - entity_type is denormalised here so callers never need a second lookup
      to determine what kind of entity this candidate represents.

    Example
    -------
    entity_id: "entity_001"
    entity_type: PERSON
    canonical_name: "Rahul Kumar"
    candidate_reason: "lexical_token_overlap"
    """

    entity_id: str = Field(..., description="Unique identifier of the candidate entity.")
    entity_type: EntityType = Field(
        ..., description="Category of the candidate entity."
    )
    canonical_name: str = Field(
        ..., description="Preferred name of the candidate entity."
    )
    candidate_reason: str = Field(
        ...,
        description=(
            "Human-readable label explaining why this entity was selected "
            "as a candidate.  Set by the generator (e.g. 'lexical_token_overlap')."
        ),
    )


# ---------------------------------------------------------------------------
# Scored Entity Candidate
# ---------------------------------------------------------------------------

class ScoredEntityCandidate(BaseModel):
    """The result of scoring a single candidate entity against an unresolved mention.

    A scored candidate is NOT a resolution decision.  It is the output of
    the candidate scoring stage — a ranked, explainable evaluation of how
    well a candidate matches the mention's surface form.

    Design notes
    ------------
    - ``score`` is the final aggregated score, always in [0.0, 1.0].
    - ``scoring_method`` identifies which scorer produced this result
      (e.g. ``"lexical_weighted_coverage"``), analogous to
      ``candidate_reason`` on EntityCandidate.
    - ``matched_representation`` records which canonical name or alias
      yielded the best score, enabling explainability without exposing
      internals.
    - Component scores (``mention_coverage``, ``candidate_coverage``,
      ``exact_match``) are exposed so the system remains evidence-backed
      and auditable.
    - This model is intentionally separate from EntityCandidate: a
      candidate is a nomination; a scored candidate is an evaluation.

    Example (partial match)
    -----------------------
    entity_id: "entity_001"
    canonical_name: "Rahul Kumar"
    score: 0.8
    scoring_method: "lexical_weighted_coverage"
    matched_representation: "rahul kumar"
    mention_coverage: 1.0
    candidate_coverage: 0.5
    exact_match: False

    Example (exact match)
    ---------------------
    entity_id: "entity_002"
    canonical_name: "Rahul Kumar"
    score: 1.0
    exact_match: True
    """

    # Identity
    entity_id: str = Field(..., description="Unique identifier of the candidate entity.")
    canonical_name: str = Field(..., description="Preferred name of the candidate entity.")

    # Scoring result
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Aggregated similarity score in [0.0, 1.0].  "
            "1.0 indicates an exact normalised match; "
            "lower values indicate partial lexical overlap."
        ),
    )
    scoring_method: str = Field(
        ...,
        description=(
            "Identifier for the scoring algorithm that produced this result "
            "(e.g. 'lexical_weighted_coverage').  Analogous to candidate_reason "
            "on EntityCandidate."
        ),
    )

    # Explainability
    matched_representation: str = Field(
        ...,
        description=(
            "The canonical_name or alias that yielded the best score.  "
            "Useful for understanding which representation drove the match."
        ),
    )
    mention_coverage: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of meaningful mention tokens covered by the overlap: "
            "overlap_count / len(mention_tokens).  "
            "1.0 means every mention token appears in the candidate representation."
        ),
    )
    candidate_coverage: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of meaningful candidate representation tokens covered by "
            "the overlap: overlap_count / len(representation_tokens).  "
            "1.0 means the representation is fully explained by the mention."
        ),
    )
    exact_match: bool = Field(
        ...,
        description=(
            "True when the normalised mention text exactly equals the "
            "candidate's canonical_name or one of its aliases.  "
            "An exact match always yields score=1.0."
        ),
    )
