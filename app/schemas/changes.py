"""Pydantic schemas for the Organisation-Wide Change Intelligence API (Stage 17).

These are the public contract of the changes endpoint -- what clients receive.
Deliberately kept separate from the internal domain models (app/models/organisation_change.py)
so the API surface can remain stable while the internal models evolve.

Design notes
------------
- OrganisationChangeTypeSchema mirrors OrganisationChangeType but is independent.
- OrganisationChangeSeveritySchema mirrors OrganisationChangeSeverity for the same reason.
- OrganisationChangeSchema mirrors OrganisationChange but uses only API-safe types.
- OrganisationChangesResponse is the top-level collection envelope.
- OrganisationChangeSummarySchema is the structured aggregate summary.
- All optional fields follow the same Optional[...] = None pattern throughout the project.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Controlled vocabularies (API layer copies)
# ---------------------------------------------------------------------------

class OrganisationChangeTypeSchema(str, Enum):
    """Change type values returned by the changes API.

    Mirrors OrganisationChangeType. Kept independent for API stability.
    """

    STATE_OPENED = "STATE_OPENED"
    STATE_STARTED = "STATE_STARTED"
    STATE_BLOCKED = "STATE_BLOCKED"
    STATE_RESOLVED = "STATE_RESOLVED"
    STATE_REGRESSED = "STATE_REGRESSED"
    STATE_REOPENED = "STATE_REOPENED"
    REPEATED_UNRESOLVED = "REPEATED_UNRESOLVED"
    RISK_ESCALATED = "RISK_ESCALATED"
    RISK_DEESCALATED = "RISK_DEESCALATED"
    NEW_DEPENDENCY = "NEW_DEPENDENCY"
    DEPENDENCY_EXPANDED = "DEPENDENCY_EXPANDED"
    IMPACT_EXPANDED = "IMPACT_EXPANDED"
    ENTITY_BECAME_STALE = "ENTITY_BECAME_STALE"


class OrganisationChangeSeveritySchema(str, Enum):
    """Change severity values returned by the changes API.

    Mirrors OrganisationChangeSeverity. Kept independent for API stability.
    """

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    INFO = "INFO"


# ---------------------------------------------------------------------------
# Change schema
# ---------------------------------------------------------------------------

class OrganisationChangeSchema(BaseModel):
    """A single detected organisation-wide change about a canonical entity.

    Every field is derived from existing ThreadLine intelligence signals.
    No field is fabricated or LLM-generated. Every change has a traceable
    evidence path: Change -> Entity -> Meeting -> Evidence.
    """

    change_id: str = Field(
        ...,
        description=(
            "Deterministic 16-character hex identifier. "
            "Identical across repeated calls given the same data."
        ),
    )

    entity_id: str = Field(
        ..., description="ID of the canonical entity this change refers to."
    )

    change_type: OrganisationChangeTypeSchema = Field(
        ..., description="The category of change detected."
    )

    severity: OrganisationChangeSeveritySchema = Field(
        ...,
        description="Fixed severity level for this change type. Never probabilistic.",
    )

    detected_at: Optional[datetime] = Field(
        default=None,
        description=(
            "Timestamp of the underlying event (meeting_date or insight observed_at). "
            "None if no temporal evidence exists. NEVER datetime.now()."
        ),
    )

    meeting_id: Optional[str] = Field(
        default=None,
        description="ID of the most relevant meeting, or None for entity-level changes.",
    )

    mention_id: Optional[str] = Field(
        default=None,
        description="ID of the primary evidence mention, or None.",
    )

    source_text: Optional[str] = Field(
        default=None,
        description="Verbatim transcript excerpt from the triggering evidence, or None.",
    )

    previous_state: Optional[str] = Field(
        default=None,
        description="Lifecycle state before this change, for state-transition changes.",
    )

    current_state: Optional[str] = Field(
        default=None,
        description="Lifecycle state after this change, for state-transition changes.",
    )

    insight_id: Optional[str] = Field(
        default=None,
        description="ID of the triggering EntityInsight, or None.",
    )

    dependency_path: Optional[list[str]] = Field(
        default=None,
        description=(
            "Ordered list of entity_ids forming a dependency chain. "
            "Populated for NEW_DEPENDENCY and DEPENDENCY_EXPANDED changes."
        ),
    )

    impact_count: Optional[int] = Field(
        default=None,
        description=(
            "Number of inbound impact associations. "
            "Populated for IMPACT_EXPANDED changes."
        ),
    )

    related_entity_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Deduplicated, sorted list of other entity_ids connected to this change "
            "(dependency targets, impact sources, etc.)."
        ),
    )

    evidence: str = Field(
        ...,
        description=(
            "Human-readable evidence summary grounded in the underlying data. "
            "Never LLM-generated."
        ),
    )


# ---------------------------------------------------------------------------
# Summary schema
# ---------------------------------------------------------------------------

class OrganisationChangeSummarySchema(BaseModel):
    """Structured aggregate summary of detected organisation-wide changes.

    All fields are deterministic counts derived from OrganisationChange records.
    NEVER generated by an LLM.
    """

    total_changes: int = Field(..., ge=0, description="Total number of detected changes.")
    critical_changes: int = Field(..., ge=0, description="Number of CRITICAL severity changes.")
    high_changes: int = Field(..., ge=0, description="Number of HIGH severity changes.")
    medium_changes: int = Field(..., ge=0, description="Number of MEDIUM severity changes.")
    info_changes: int = Field(..., ge=0, description="Number of INFO severity changes.")

    newly_blocked_entities: list[str] = Field(
        default_factory=list,
        description="Entity IDs newly detected as BLOCKED.",
    )

    newly_resolved_entities: list[str] = Field(
        default_factory=list,
        description="Entity IDs newly detected as RESOLVED.",
    )

    regressed_entities: list[str] = Field(
        default_factory=list,
        description="Entity IDs that experienced a STATE_REGRESSED change.",
    )

    reopened_entities: list[str] = Field(
        default_factory=list,
        description="Entity IDs with a STATE_REOPENED change.",
    )

    new_dependency_count: int = Field(
        ..., ge=0, description="Number of NEW_DEPENDENCY changes detected."
    )

    expanded_impact_count: int = Field(
        ..., ge=0, description="Number of IMPACT_EXPANDED changes detected."
    )

    stale_entities: list[str] = Field(
        default_factory=list,
        description="Entity IDs newly detected as stale.",
    )

    changes_by_entity: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Mapping of entity_id to list of change_type values detected for that entity. "
            "Allows grouping multiple changes per entity."
        ),
    )


# ---------------------------------------------------------------------------
# Top-level response envelopes
# ---------------------------------------------------------------------------

class OrganisationChangesResponse(BaseModel):
    """Response envelope for GET /api/v1/changes.

    Contains the deterministic, ordered list of all detected organisation-wide
    changes and aggregate counts.
    """

    total_changes: int = Field(
        ..., ge=0, description="Total number of changes returned."
    )

    critical_changes: int = Field(
        ..., ge=0, description="Number of CRITICAL severity changes returned."
    )

    high_changes: int = Field(
        ..., ge=0, description="Number of HIGH severity changes returned."
    )

    medium_changes: int = Field(
        ..., ge=0, description="Number of MEDIUM severity changes returned."
    )

    info_changes: int = Field(
        ..., ge=0, description="Number of INFO severity changes returned."
    )

    changes: list[OrganisationChangeSchema] = Field(
        default_factory=list,
        description=(
            "Deterministically ordered list of detected organisation-wide changes. "
            "Ordered by: severity DESC, change_type priority ASC, "
            "detected_at DESC, entity_id ASC, change_id ASC."
        ),
    )

    evaluated_at: datetime = Field(
        ...,
        description="The reference datetime at which changes were evaluated.",
    )


class OrganisationChangeSummaryResponse(BaseModel):
    """Response envelope for GET /api/v1/changes/summary."""

    summary: OrganisationChangeSummarySchema = Field(
        ...,
        description="Structured aggregate summary of detected organisation-wide changes.",
    )

    evaluated_at: datetime = Field(
        ...,
        description="The reference datetime at which the summary was computed.",
    )
