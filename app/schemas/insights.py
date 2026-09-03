"""Pydantic schemas for the Insight & Change Detection Engine API.

These are the *public contract* of the insights endpoint — what clients
receive.  They are deliberately kept separate from the internal domain
models (app/models/insights.py) so the API surface can remain stable
while the internal models evolve.

Design notes
------------
- InsightTypeSchema mirrors InsightType but is independent so the two
  layers can diverge in the future without a breaking API change.
- InsightSeveritySchema mirrors InsightSeverity for the same reason.
- EntityInsightSchema mirrors EntityInsight but uses only API-safe types.
- EntityInsightsResponse includes a convenience insight_count field so
  clients can quickly check the count without iterating the insights list.
- All optional fields follow the same Optional[...] = None pattern used
  throughout the schemas layer (correlation.py, temporal.py, memory.py).
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Controlled vocabularies (API layer copies)
# ---------------------------------------------------------------------------

class InsightTypeSchema(str, Enum):
    """Insight type values returned by the insights API.

    Mirrors the domain model's InsightType enum.
    Kept as an independent enum so the API surface can evolve separately
    from the internal model if needed.

    UNKNOWN_STATE        — entity has observations but no meaningful state.
    STATE_CHANGED        — entity moved from one state to another.
    ISSUE_BLOCKED        — entity entered BLOCKED state.
    ISSUE_RESOLVED       — entity entered RESOLVED state.
    REOPEN_ATTEMPT       — observation attempted to reopen a RESOLVED entity.
    REPEATED_OBSERVATION — entity observed multiple times in one meeting
                           without a state transition.
    STALE_ENTITY         — entity not observed for a configurable period.
    """

    UNKNOWN_STATE = "UNKNOWN_STATE"
    STATE_CHANGED = "STATE_CHANGED"
    ISSUE_BLOCKED = "ISSUE_BLOCKED"
    ISSUE_RESOLVED = "ISSUE_RESOLVED"
    REOPEN_ATTEMPT = "REOPEN_ATTEMPT"
    REPEATED_OBSERVATION = "REPEATED_OBSERVATION"
    STALE_ENTITY = "STALE_ENTITY"


class InsightSeveritySchema(str, Enum):
    """Insight severity values returned by the insights API.

    Mirrors the domain model's InsightSeverity enum.

    INFO     — informational; noteworthy but requires no immediate action.
    WARNING  — indicates a potential problem or risk worth attention.
    CRITICAL — (reserved) urgent condition requiring immediate action.
    """

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Insight schema
# ---------------------------------------------------------------------------

class EntityInsightSchema(BaseModel):
    """A single derived insight about a canonical entity.

    Returned as an element of EntityInsightsResponse.insights.
    Every field carries meaningful, evidence-grounded information.
    """

    insight_id: str = Field(
        ...,
        description=(
            "Deterministic 16-character identifier for this insight.  "
            "Derived from SHA-256 of (entity_id, insight_type, meeting_id, "
            "obs_index).  Identical for the same event across calls."
        ),
    )
    entity_id: str = Field(
        ..., description="ID of the canonical entity this insight refers to."
    )
    insight_type: InsightTypeSchema = Field(
        ..., description="The category of this insight."
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
    severity: InsightSeveritySchema = Field(
        ...,
        description=(
            "Fixed severity level for this insight type.  "
            "Determined by a static mapping — not a probabilistic score."
        ),
    )
    observed_at: datetime = Field(
        ...,
        description=(
            "Timestamp at which the underlying event was observed (ISO-8601).  "
            "For timeline-derived insights: the meeting_date.  "
            "For STALE_ENTITY / UNKNOWN_STATE: the entity's last_observed_at."
        ),
    )
    related_meeting_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of the most directly associated meeting.  "
            "Null for STALE_ENTITY and UNKNOWN_STATE."
        ),
    )
    evidence: str = Field(
        ...,
        description=(
            "Human-readable evidence string that triggered this insight.  "
            "For timeline insights: the observation evidence_text.  "
            "For STALE_ENTITY / UNKNOWN_STATE: a descriptive summary."
        ),
    )
    deterministic_sort_key: str = Field(
        ...,
        description=(
            "Pre-computed sort key of the form "
            "'observed_at_iso|entity_id|insight_type|insight_id'.  "
            "Used for stable, reproducible ordering."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "insight_id": "a3f9c1d2e4b50617",
                    "entity_id": "entity_001",
                    "insight_type": "STATE_CHANGED",
                    "title": "State changed",
                    "description": "The entity transitioned from OPEN to IN_PROGRESS.",
                    "severity": "INFO",
                    "observed_at": "2026-08-08T10:00:00Z",
                    "related_meeting_id": "meeting_002",
                    "evidence": "Started working on the payment API issue.",
                    "deterministic_sort_key": (
                        "2026-08-08T10:00:00+00:00|entity_001|STATE_CHANGED|a3f9c1d2e4b50617"
                    ),
                },
                {
                    "insight_id": "b7e2a0f3c91d4825",
                    "entity_id": "entity_002",
                    "insight_type": "STALE_ENTITY",
                    "title": "Stale entity",
                    "description": (
                        "Entity 'database timeout' has not been observed for 45 days "
                        "and is not RESOLVED.  Current state: OPEN."
                    ),
                    "severity": "WARNING",
                    "observed_at": "2026-07-01T10:00:00Z",
                    "related_meeting_id": None,
                    "evidence": "Entity last observed at 2026-07-01T10:00:00+00:00, 45 days ago.",
                    "deterministic_sort_key": (
                        "2026-07-01T10:00:00+00:00|entity_002|STALE_ENTITY|b7e2a0f3c91d4825"
                    ),
                },
            ]
        }
    }


# ---------------------------------------------------------------------------
# Insights response schema
# ---------------------------------------------------------------------------

class EntityInsightsResponse(BaseModel):
    """Response body for GET /api/v1/entities/{entity_id}/insights.

    Returns all derived insights for a canonical entity: actionable
    intelligence computed deterministically from the entity's temporal
    lifecycle history and organisational memory.

    Always returns HTTP 200 when the entity exists.
    Returns HTTP 404 if the entity_id does not exist.

    When the entity exists but has no applicable insights:
      - insight_count is 0.
      - insights is an empty list.

    This endpoint answers:
      'What changed for this entity, and which changes are important?'

    Read-only invariant: this endpoint never creates or modifies entities,
    mentions, or resolution state.  It never re-runs extraction, candidate
    generation, scoring, or resolution.

    Ordering: insights are sorted by
      (observed_at ASC, entity_id ASC, insight_type ASC, insight_id ASC).
    This ordering is deterministic and reproducible.
    """

    entity_id: str = Field(
        ..., description="Unique identifier of the canonical entity."
    )
    insight_count: int = Field(
        ...,
        ge=0,
        description=(
            "Total number of insights for this entity.  "
            "Equals len(insights).  Provided as a convenience field."
        ),
    )
    insights: list[EntityInsightSchema] = Field(
        default_factory=list,
        description=(
            "Ordered list of derived insights for this entity.  "
            "Empty when no insights apply.  "
            "Ordered by (observed_at ASC, entity_id ASC, insight_type ASC, "
            "insight_id ASC)."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "entity_id": "entity_001",
                    "insight_count": 2,
                    "insights": [
                        {
                            "insight_id": "a3f9c1d2e4b50617",
                            "entity_id": "entity_001",
                            "insight_type": "STATE_CHANGED",
                            "title": "State changed",
                            "description": "The entity transitioned from UNKNOWN to IN_PROGRESS.",
                            "severity": "INFO",
                            "observed_at": "2026-08-01T10:00:00Z",
                            "related_meeting_id": "meeting_001",
                            "evidence": "Started working on the payment API issue.",
                            "deterministic_sort_key": (
                                "2026-08-01T10:00:00+00:00|entity_001|STATE_CHANGED|a3f9c1d2e4b50617"
                            ),
                        },
                        {
                            "insight_id": "c8d1b4e5f2a09318",
                            "entity_id": "entity_001",
                            "insight_type": "ISSUE_RESOLVED",
                            "title": "Issue resolved",
                            "description": "The entity transitioned from IN_PROGRESS to RESOLVED.",
                            "severity": "INFO",
                            "observed_at": "2026-08-15T10:00:00Z",
                            "related_meeting_id": "meeting_003",
                            "evidence": "The payment API issue has been resolved.",
                            "deterministic_sort_key": (
                                "2026-08-15T10:00:00+00:00|entity_001|ISSUE_RESOLVED|c8d1b4e5f2a09318"
                            ),
                        },
                    ],
                },
                {
                    "entity_id": "entity_002",
                    "insight_count": 0,
                    "insights": [],
                },
            ]
        }
    }
