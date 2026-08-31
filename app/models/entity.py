"""Internal domain models for the Entity Resolution system.

These are the authoritative representations of canonical entities and entity
mentions inside Threadline.  They are *not* tied to any API schema or
persistence format — those layers translate to/from these models as needed.

Design notes
------------
- EntityType, ResolutionStatus, and ResolutionOutcome are StrEnums so Pydantic
  v2 serialises them as plain strings (e.g., "PERSON") without extra model config.
- CanonicalEntity is intentionally separate from EntityMention.
  An entity is an organisational object; a mention is an observed reference.
- entity_id on EntityMention is Optional to support unresolved mentions.
- ResolutionStatus tracks the *stored state* of a mention (what it currently is).
- ResolutionOutcome is the *decision engine output* (what the engine decided this
  run).  They are deliberately separate so the decision can be inspected
  independently of the persisted state.
- ResolutionDecision is the explainable output of the Resolution Decision stage.
  It records why the system made its choice, enabling auditability.

Future pipeline stages (embeddings, cross-meeting correlation, human review)
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
    """The stored resolution state of an entity mention.

    RESOLVED   — the mention was matched to a known canonical entity.
    UNRESOLVED — no safe match was found; the mention is stored as-is.
    AMBIGUOUS  — multiple plausible candidates were found but no single
                 candidate had sufficient margin over the others.  The
                 mention is NOT assigned an entity_id.

    Design: kept as an enum (not bool) so additional states can be added
    later (e.g. PENDING_REVIEW, REJECTED) without an API breaking change.
    """

    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


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


# ---------------------------------------------------------------------------
# Resolution Outcome  (decision engine output)
# ---------------------------------------------------------------------------

class ResolutionOutcome(str, Enum):
    """The decision produced by the Resolution Decision Engine for a single mention.

    This is the *output* of running the decision policy against scored
    candidates.  It is deliberately separate from ResolutionStatus:

    - ResolutionStatus  — the *stored state* of a mention (what it currently is).
    - ResolutionOutcome — the *engine's decision* for this run (what was decided).

    RESOLVED   — the top candidate exceeded the confidence threshold and had
                 sufficient margin over the second candidate.  The mention's
                 entity_id is set to the top candidate's entity_id.

    AMBIGUOUS  — the top candidate exceeded the confidence threshold but the
                 margin over the second candidate was too small to act safely.
                 The mention's entity_id remains None.

    UNRESOLVED — no candidate exceeded the confidence threshold, or there were
                 no candidates at all.  The mention's entity_id remains None.

    Design note: additional outcomes (e.g. PENDING_REVIEW) can be added later
    without restructuring the decision engine or the stored-state enum.
    """

    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"


# ---------------------------------------------------------------------------
# Resolution Decision  (explainable output of the decision engine)
# ---------------------------------------------------------------------------

class ResolutionDecision(BaseModel):
    """The explainable result of the Resolution Decision Engine for one mention.

    A ResolutionDecision captures *what* the engine decided and *why*.  It is
    produced by a ResolutionPolicy and orchestrated by ResolutionService.

    Important: ``top_score`` and ``second_score`` are lexical similarity scores
    in [0.0, 1.0] produced by the candidate scorer.  They are NOT probabilities.
    A score of 0.92 means "the candidate received a lexical similarity score of
    0.92 under the scoring function" — not "there is a 92% chance this is correct".

    Fields
    ------
    mention_id:
        The mention this decision was made for.
    outcome:
        The engine's decision (RESOLVED / AMBIGUOUS / UNRESOLVED).
    selected_entity_id:
        The entity_id chosen when outcome is RESOLVED; None otherwise.
    top_score:
        Lexical similarity score of the highest-ranked candidate.  None when
        there are no candidates.
    second_score:
        Lexical similarity score of the second-ranked candidate.  None when
        there is only one candidate or no candidates.
    score_margin:
        top_score - second_score.  None when second_score is None.
    reason:
        Human-readable explanation of the decision (e.g. why AMBIGUOUS).

    Example (RESOLVED)
    ------------------
    outcome: RESOLVED
    selected_entity_id: "entity_001"
    top_score: 0.94
    second_score: 0.40
    score_margin: 0.54
    reason: "Top candidate exceeded the confidence threshold and had sufficient "
            "margin over the second candidate."

    Example (AMBIGUOUS)
    -------------------
    outcome: AMBIGUOUS
    selected_entity_id: None
    top_score: 0.91
    second_score: 0.90
    score_margin: 0.01
    reason: "Top candidate exceeded the confidence threshold but was too close "
            "to the second candidate."
    """

    mention_id: str = Field(
        ..., description="ID of the mention this decision was made for."
    )
    outcome: ResolutionOutcome = Field(
        ...,
        description=(
            "The engine's decision: RESOLVED, AMBIGUOUS, or UNRESOLVED.  "
            "See ResolutionOutcome for semantics."
        ),
    )
    selected_entity_id: Optional[str] = Field(
        default=None,
        description=(
            "The entity_id selected by the engine when outcome is RESOLVED.  "
            "Always None for AMBIGUOUS and UNRESOLVED decisions."
        ),
    )
    top_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Lexical similarity score of the highest-ranked candidate in [0.0, 1.0].  "
            "This is NOT a probability — it is the raw output of the scoring function.  "
            "None when there are no candidates."
        ),
    )
    second_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Lexical similarity score of the second-ranked candidate in [0.0, 1.0].  "
            "None when there are fewer than two candidates."
        ),
    )
    score_margin: Optional[float] = Field(
        default=None,
        description=(
            "Difference between top_score and second_score (top - second).  "
            "None when second_score is None.  "
            "A larger margin indicates the top candidate is more clearly the best match."
        ),
    )
    reason: str = Field(
        ...,
        description="Human-readable explanation of why this decision was made.",
    )
