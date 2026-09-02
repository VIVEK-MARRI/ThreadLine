"""Internal domain models for the Organisational Memory layer.

These are the authoritative representations of organisational memory inside
Threadline.  They are *not* tied to any API schema or persistence format —
those layers translate to/from these models as needed.

Design notes
------------
- MemoryFactType is a StrEnum (like EntityType, TemporalState) so Pydantic v2
  serialises it as a plain string without extra model config.
- EntityMemoryFact is the atomic unit of memory — one grounded, evidence-backed
  piece of structured knowledge about a canonical entity.
- EntityMemory is the top-level aggregation result for one canonical entity.
  It is computed on read and never persisted.
- first_observed_at / last_observed_at use actual meeting timestamps (meeting_date)
  from EntityObservation records.  They are NEVER set to datetime.now().
- observation_count counts total resolved observations; meeting_count counts
  distinct meetings — these are separate metrics.

Relationship to other models
-----------------------------
- EntityMemory aggregates data from CanonicalEntity, EntityTimeline, and
  EntityCorrelation.  It is NOT a superset of any single existing model.
- TemporalState (from models/temporal.py) is imported and reused — memory does
  not define its own state vocabulary.

These models are populated exclusively by OrganisationalMemoryService and must
never be modified by other pipeline stages.

Invariants
----------
- EntityMemory.first_observed_at is None iff observation_count == 0.
- EntityMemory.last_observed_at is None iff observation_count == 0.
- EntityMemory.observation_count == len(timeline) from TemporalStateService.
- EntityMemory.meeting_count == len({obs.meeting_id for obs in timeline}).
- EntityMemoryFact with fact_type CURRENT_STATE has no source evidence
  (source_meeting_id=None, source_mention_id=None, observed_at=None).
- EntityMemoryFact with fact_type STATE_TRANSITION has all evidence fields set.
- EntityMemoryFact with fact_type REPEATED_OBSERVATION has source_meeting_id
  and observed_at set, but source_mention_id=None (it is meeting-level).
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from app.models.entity import EntityType
from app.models.temporal import TemporalState


# ---------------------------------------------------------------------------
# Controlled vocabulary
# ---------------------------------------------------------------------------

class MemoryFactType(str, Enum):
    """The category of a structured memory fact about a canonical entity.

    Each value represents a distinct kind of grounded, evidence-backed
    knowledge that the Organisational Memory layer can assert.

    FIRST_OBSERVED
        The earliest recorded observation of this entity across all meetings.
        Evidence: the first resolved mention's meeting and mention IDs.

    LAST_OBSERVED
        The most recent observation of this entity across all meetings.
        Only emitted when observation_count >= 2 (otherwise identical to
        FIRST_OBSERVED).
        Evidence: the last resolved mention's meeting and mention IDs.

    CURRENT_STATE
        The entity's current (most recent) temporal lifecycle state, as
        determined by TemporalStateService.  This is an aggregate fact —
        not caused by any single observation — so it carries no evidence
        pointers.

    STATE_TRANSITION
        A valid lifecycle state transition that occurred during the entity's
        observed history (e.g. IN_PROGRESS → BLOCKED).
        Only valid transitions (transition_occurred=True) are recorded.
        Invalid transitions (recorded by TemporalStateService as
        is_valid_transition=False) are NOT represented here.
        Evidence: the observation that triggered the transition.

    REPEATED_OBSERVATION
        A meeting in which this entity was observed two or more times.
        This is meeting-level evidence, not mention-level, so
        source_mention_id is None.
    """

    FIRST_OBSERVED = "FIRST_OBSERVED"
    LAST_OBSERVED = "LAST_OBSERVED"
    CURRENT_STATE = "CURRENT_STATE"
    STATE_TRANSITION = "STATE_TRANSITION"
    REPEATED_OBSERVATION = "REPEATED_OBSERVATION"


# ---------------------------------------------------------------------------
# Memory Fact — a single grounded piece of organisational knowledge
# ---------------------------------------------------------------------------

class EntityMemoryFact(BaseModel):
    """A single evidence-backed fact about a canonical entity.

    Every fact is fully traceable: every field that can be set to a
    meaningful value IS set to that value.  Nothing is invented.

    Evidence fields (source_meeting_id, source_mention_id, observed_at) are
    set when applicable and explicitly None otherwise.  The absence of an
    evidence pointer is always intentional and documented in the MemoryFactType
    docstring.

    Examples
    --------
    FIRST_OBSERVED
        fact_type:         FIRST_OBSERVED
        value:             "2026-08-01T10:00:00+00:00"
        source_meeting_id: "meeting_001"
        source_mention_id: "mention_abc"
        observed_at:       2026-08-01T10:00:00Z
        detail:            "Sprint Planning"   (meeting title)

    CURRENT_STATE
        fact_type:         CURRENT_STATE
        value:             "BLOCKED"
        source_meeting_id: None
        source_mention_id: None
        observed_at:       None
        detail:            None

    STATE_TRANSITION
        fact_type:         STATE_TRANSITION
        value:             "IN_PROGRESS → BLOCKED"
        source_meeting_id: "meeting_002"
        source_mention_id: "mention_def"
        observed_at:       2026-08-08T10:00:00Z
        detail:            "Weekly Sync"        (meeting title)

    REPEATED_OBSERVATION
        fact_type:         REPEATED_OBSERVATION
        value:             "3"                 (observation count in this meeting)
        source_meeting_id: "meeting_003"
        source_mention_id: None
        observed_at:       2026-08-15T10:00:00Z
        detail:            "Retrospective"      (meeting title)
    """

    fact_type: MemoryFactType = Field(
        ..., description="The category of this memory fact."
    )
    value: str = Field(
        ...,
        description=(
            "The primary value of this fact as a human-readable string.  "
            "For FIRST_OBSERVED and LAST_OBSERVED: ISO-8601 datetime string.  "
            "For CURRENT_STATE: the state name (e.g. 'BLOCKED').  "
            "For STATE_TRANSITION: 'FROM_STATE → TO_STATE'.  "
            "For REPEATED_OBSERVATION: the observation count as a string."
        ),
    )
    source_meeting_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of the meeting that produced this fact.  "
            "None for CURRENT_STATE (it is an aggregate, not a single-meeting fact)."
        ),
    )
    source_mention_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of the specific EntityMention that produced this fact.  "
            "None for CURRENT_STATE and REPEATED_OBSERVATION "
            "(meeting-level or aggregate facts)."
        ),
    )
    observed_at: Optional[datetime] = Field(
        default=None,
        description=(
            "The meeting_date of the meeting where this fact was observed.  "
            "None for CURRENT_STATE."
        ),
    )
    detail: Optional[str] = Field(
        default=None,
        description=(
            "Additional human-readable context for this fact.  "
            "For observation-backed facts: the meeting title.  "
            "None for CURRENT_STATE."
        ),
    )


# ---------------------------------------------------------------------------
# Entity Memory — the complete organisational knowledge record for one entity
# ---------------------------------------------------------------------------

class EntityMemory(BaseModel):
    """The complete organisational memory record for a single canonical entity.

    EntityMemory aggregates data from:
      - CanonicalEntity (identity fields)
      - EntityTimeline from TemporalStateService (state, transitions, observations)

    It is the output of OrganisationalMemoryService.get_entity_memory() and is
    computed on read — never persisted.

    When the entity has no resolved observations:
      - first_observed_at and last_observed_at are None
      - meeting_count and observation_count are 0
      - current_state is TemporalState.UNKNOWN
      - facts contains only a single CURRENT_STATE fact (value='UNKNOWN')

    Ordering of facts
    -----------------
    Facts are ordered as follows for determinism:
      1. FIRST_OBSERVED (if present)
      2. LAST_OBSERVED (if present)
      3. CURRENT_STATE (always present)
      4. STATE_TRANSITION facts, chronological order
      5. REPEATED_OBSERVATION facts, ordered by (meeting_date ASC, meeting_id ASC)
    """

    # Identity
    entity_id: str = Field(
        ..., description="Unique identifier of the canonical entity."
    )
    canonical_name: str = Field(
        ..., description="Preferred, normalised name of the canonical entity."
    )
    entity_type: EntityType = Field(
        ..., description="Category of the canonical entity (PERSON, ISSUE, etc.)."
    )

    # Temporal summary
    first_observed_at: Optional[datetime] = Field(
        default=None,
        description=(
            "The meeting_date of the earliest resolved observation of this entity.  "
            "None when the entity has no resolved mentions."
        ),
    )
    last_observed_at: Optional[datetime] = Field(
        default=None,
        description=(
            "The meeting_date of the most recent resolved observation of this entity.  "
            "None when the entity has no resolved mentions."
        ),
    )

    # Counts
    meeting_count: int = Field(
        ...,
        ge=0,
        description=(
            "Number of distinct meetings in which this entity has at least one "
            "resolved observation."
        ),
    )
    observation_count: int = Field(
        ...,
        ge=0,
        description=(
            "Total number of resolved observations (mentions) for this entity "
            "across all meetings.  May be greater than meeting_count when the "
            "entity appears multiple times in the same meeting."
        ),
    )

    # Current lifecycle state
    current_state: TemporalState = Field(
        ...,
        description=(
            "The current (most recent) lifecycle state of this entity, as "
            "determined by the Temporal State Engine.  "
            "UNKNOWN when there are no observations or no state-bearing evidence."
        ),
    )

    # Structured knowledge facts
    facts: list[EntityMemoryFact] = Field(
        default_factory=list,
        description=(
            "Ordered list of evidence-backed facts the organisation knows about "
            "this entity.  Always contains at least one fact (CURRENT_STATE).  "
            "Ordered by: FIRST_OBSERVED, LAST_OBSERVED, CURRENT_STATE, "
            "STATE_TRANSITION (chronological), REPEATED_OBSERVATION "
            "(by meeting_date ASC, meeting_id ASC)."
        ),
    )
