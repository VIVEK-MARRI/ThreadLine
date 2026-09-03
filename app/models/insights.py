"""Internal domain models for the Insight & Change Detection Engine.

These are the authoritative representations of derived insights inside
Threadline.  They are *not* tied to any API schema or persistence format —
those layers translate to/from these models as needed.

Design notes
------------
- InsightType is a str, Enum (like EntityType, TemporalState, MemoryFactType)
  so Pydantic v2 serialises it as a plain string without extra model config.
- InsightSeverity is a str, Enum with three levels: INFO, WARNING, CRITICAL.
- EntityInsight is the atomic unit of derived intelligence — one insight
  about one canonical entity, fully grounded in observable evidence.
- insight_id is a deterministic identifier derived from
  (entity_id, insight_type, related_meeting_id, observation_index) so that
  running the service multiple times always produces the same insight_id
  for the same underlying event.
- deterministic_sort_key is a pre-computed tuple string used for stable
  ordering without re-sorting on every access.

Relationship to other models
-----------------------------
- EntityInsight is derived from EntityTimeline (TemporalStateService) and
  EntityMemory (OrganisationalMemoryService).  It is NOT a superset of either.
- TemporalState (from models/temporal.py) and MemoryFactType (from
  models/memory.py) are imported and referenced in service logic — not here.
- EntityInsight is computed on read and never persisted.

Invariants
----------
- EntityInsight.observed_at always contains a meaningful datetime.
  For timeline-derived insights it is the StateObservation.meeting_date.
  For STALE_ENTITY and UNKNOWN_STATE it is EntityMemory.last_observed_at
  (the most recent observation timestamp).
- EntityInsight.related_meeting_id is None for STALE_ENTITY and
  UNKNOWN_STATE (no single meeting is responsible).
- Severity mapping (deterministic, no probabilistic scores):
    ISSUE_RESOLVED      → INFO
    STATE_CHANGED       → INFO
    REPEATED_OBSERVATION → INFO
    UNKNOWN_STATE       → INFO
    STALE_ENTITY        → WARNING
    ISSUE_BLOCKED       → WARNING
    REOPEN_ATTEMPT      → WARNING

These models are populated exclusively by InsightService and must
never be modified by other pipeline stages.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

class InsightType(str, Enum):
    """The category of a derived insight about a canonical entity.

    Each value represents a distinct kind of actionable intelligence
    the Insight Engine can assert based on the entity's temporal history.

    UNKNOWN_STATE
        No meaningful lifecycle state has been determined for the entity,
        even though observations exist.

    STATE_CHANGED
        The entity moved from one valid lifecycle state to another.
        Generated for every valid state transition.

    ISSUE_BLOCKED
        The entity entered the BLOCKED lifecycle state.
        Generated in addition to STATE_CHANGED when the transition target
        is BLOCKED.

    ISSUE_RESOLVED
        The entity entered the RESOLVED lifecycle state.
        Generated in addition to STATE_CHANGED when the transition target
        is RESOLVED.

    REOPEN_ATTEMPT
        An observation attempted to transition a RESOLVED entity into
        another lifecycle state (e.g. RESOLVED → IN_PROGRESS).
        The temporal state remains RESOLVED; this insight records the
        attempt.

    REPEATED_OBSERVATION
        The same entity continued appearing in a meeting without triggering
        a meaningful state transition, indicating sustained attention
        without forward progress.

    STALE_ENTITY
        The entity has not been observed for a configurable period (default
        30 days), has at least one observation, and is not RESOLVED.
    """

    UNKNOWN_STATE = "UNKNOWN_STATE"
    STATE_CHANGED = "STATE_CHANGED"
    ISSUE_BLOCKED = "ISSUE_BLOCKED"
    ISSUE_RESOLVED = "ISSUE_RESOLVED"
    REOPEN_ATTEMPT = "REOPEN_ATTEMPT"
    REPEATED_OBSERVATION = "REPEATED_OBSERVATION"
    STALE_ENTITY = "STALE_ENTITY"


class InsightSeverity(str, Enum):
    """The severity level of a derived insight.

    Severity is deterministic — it is a fixed property of each InsightType,
    not a probabilistic score.  No confidence values are used.

    INFO     — informational; the insight records a noteworthy event but
               requires no immediate action.
    WARNING  — the insight indicates a potential problem or risk that
               warrants attention.
    CRITICAL — (reserved for future use) the insight signals an urgent
               condition requiring immediate action.

    Deterministic mapping:
        ISSUE_RESOLVED       → INFO
        STATE_CHANGED        → INFO
        REPEATED_OBSERVATION → INFO
        UNKNOWN_STATE        → INFO
        STALE_ENTITY         → WARNING
        ISSUE_BLOCKED        → WARNING
        REOPEN_ATTEMPT       → WARNING
    """

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Deterministic severity mapping
# ---------------------------------------------------------------------------

#: Maps each InsightType to its fixed InsightSeverity.
#: This mapping is the single source of truth — InsightService uses it
#: to avoid hard-coding severity in every rule.
INSIGHT_SEVERITY: dict[InsightType, InsightSeverity] = {
    InsightType.ISSUE_RESOLVED: InsightSeverity.INFO,
    InsightType.STATE_CHANGED: InsightSeverity.INFO,
    InsightType.REPEATED_OBSERVATION: InsightSeverity.INFO,
    InsightType.UNKNOWN_STATE: InsightSeverity.INFO,
    InsightType.STALE_ENTITY: InsightSeverity.WARNING,
    InsightType.ISSUE_BLOCKED: InsightSeverity.WARNING,
    InsightType.REOPEN_ATTEMPT: InsightSeverity.WARNING,
}


# ---------------------------------------------------------------------------
# Entity Insight — the atomic unit of derived intelligence
# ---------------------------------------------------------------------------

class EntityInsight(BaseModel):
    """A single derived insight about a canonical entity.

    An EntityInsight is the atomic output of the Insight & Change Detection
    Engine.  It is always grounded in evidence produced by the Temporal State
    Engine or the Organisational Memory Engine — never invented.

    Every insight is fully deterministic: the same repository state always
    produces the same insight_id, same title, same description, and same
    ordering position.

    Fields
    ------
    insight_id
        Deterministic identifier.  Derived from a SHA-256 hash of
        (entity_id, insight_type, related_meeting_id or "", obs_index or "").
        Truncated to 16 hex characters for readability.
        Identical across repeated service invocations given the same data.

    entity_id
        The canonical entity this insight refers to.

    insight_type
        The category of this insight (see InsightType).

    title
        Short, human-readable summary of the insight.

    description
        Detailed, evidence-grounded explanation of what happened and why
        this insight was generated.

    severity
        The fixed severity level for this insight type (see InsightSeverity).
        Determined by INSIGHT_SEVERITY mapping — never computed dynamically.

    observed_at
        The timestamp at which the underlying event was observed.
        For timeline-derived insights: the meeting_date of the observation.
        For STALE_ENTITY / UNKNOWN_STATE: the entity's last_observed_at.

    related_meeting_id
        The ID of the meeting most directly associated with this insight.
        None for STALE_ENTITY and UNKNOWN_STATE (no single meeting is
        the cause).

    evidence
        A human-readable string containing the key evidence that triggered
        this insight (e.g., the evidence_text of the triggering observation,
        or a staleness summary).

    deterministic_sort_key
        A pre-computed string of the form
        "observed_at_iso|entity_id|insight_type|insight_id" used for
        stable, reproducible ordering without re-sorting on each access.

    Example (STATE_CHANGED)
    -----------------------
    insight_id:          "a3f9c1d2e4b50617"
    entity_id:           "entity_001"
    insight_type:        STATE_CHANGED
    title:               "State changed"
    description:         "The entity transitioned from OPEN to IN_PROGRESS."
    severity:            INFO
    observed_at:         2026-08-08T10:00:00Z
    related_meeting_id:  "meeting_002"
    evidence:            "Started working on the payment API issue."
    deterministic_sort_key: "2026-08-08T10:00:00+00:00|entity_001|STATE_CHANGED|a3f9c1d2e4b50617"

    Example (STALE_ENTITY)
    ----------------------
    insight_id:          "b7e2a0f3c91d4825"
    entity_id:           "entity_002"
    insight_type:        STALE_ENTITY
    title:               "Stale entity"
    description:         "Entity 'database timeout' has not been observed for 45 days ..."
    severity:            WARNING
    observed_at:         2026-07-01T10:00:00Z   (last_observed_at)
    related_meeting_id:  None
    evidence:            "Entity last observed at 2026-07-01T10:00:00+00:00, 45 days ago."
    deterministic_sort_key: "2026-07-01T10:00:00+00:00|entity_002|STALE_ENTITY|b7e2a0f3c91d4825"
    """

    insight_id: str = Field(
        ...,
        description=(
            "Deterministic identifier derived from a SHA-256 hash of "
            "(entity_id, insight_type, related_meeting_id, observation_index).  "
            "Truncated to 16 hex characters.  "
            "Identical for the same underlying event across repeated calls."
        ),
    )

    entity_id: str = Field(
        ..., description="ID of the canonical entity this insight refers to."
    )

    insight_type: InsightType = Field(
        ..., description="The category of this insight (see InsightType)."
    )

    title: str = Field(
        ..., description="Short, human-readable summary of the insight."
    )

    description: str = Field(
        ...,
        description=(
            "Detailed, evidence-grounded explanation of what happened and "
            "why this insight was generated."
        ),
    )

    severity: InsightSeverity = Field(
        ...,
        description=(
            "Fixed severity level for this insight type.  "
            "Determined by a static mapping — not a probabilistic score."
        ),
    )

    observed_at: datetime = Field(
        ...,
        description=(
            "Timestamp at which the underlying event was observed.  "
            "For timeline-derived insights: the meeting_date of the observation.  "
            "For STALE_ENTITY / UNKNOWN_STATE: the entity's last_observed_at."
        ),
    )

    related_meeting_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of the meeting most directly associated with this insight.  "
            "None for STALE_ENTITY and UNKNOWN_STATE (no single meeting is "
            "the cause of these conditions)."
        ),
    )

    evidence: str = Field(
        ...,
        description=(
            "Human-readable evidence string that triggered this insight.  "
            "For timeline-derived insights: typically the evidence_text of "
            "the triggering observation.  "
            "For STALE_ENTITY / UNKNOWN_STATE: a descriptive staleness or "
            "state summary string."
        ),
    )

    deterministic_sort_key: str = Field(
        ...,
        description=(
            "Pre-computed sort key of the form "
            "'observed_at_iso|entity_id|insight_type|insight_id'.  "
            "Used for stable, reproducible ordering of insights."
        ),
    )
