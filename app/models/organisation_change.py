"""Internal domain models for the Organisation-Wide Change Intelligence layer (Stage 17).

These models represent deterministic, evidence-backed records of organisation-wide
changes detected by aggregating signals across all ThreadLine intelligence layers.

Design notes
------------
- OrganisationChangeType is a StrEnum consistent with all other enums in the project.
- OrganisationChangeSeverity mirrors InsightSeverity / AttentionLevel vocabulary.
- OrganisationChange is the atomic unit of change intelligence - one detected
  change about one canonical entity, fully grounded in traceable evidence.
- change_id is deterministic: sha256(entity_id:change_type:transition_key:meeting_id)[:16].
  The same underlying event always produces the same change_id across repeated calls.
- detected_at uses meeting_date or insight observed_at. datetime.now() is NEVER called.
- All list fields are deterministically sorted.

Change type semantics
---------------------
STATE_OPENED        -- entity first observed in OPEN state (UNKNOWN -> OPEN transition)
STATE_STARTED       -- entity transitioned to IN_PROGRESS
STATE_BLOCKED       -- entity transitioned to BLOCKED state
STATE_RESOLVED      -- entity transitioned to RESOLVED state
STATE_REGRESSED     -- entity moved to a worse state (IN_PROGRESS->BLOCKED or OPEN->BLOCKED)
STATE_REOPENED      -- a RESOLVED entity had a reopen attempt (REOPEN_ATTEMPT insight)
REPEATED_UNRESOLVED -- entity appears multiple times without state progress
RISK_ESCALATED      -- entity is observed in BLOCKED state with CRITICAL attention.
                       This is a CURRENT-STATE SIGNAL, not a proven historical risk transition.
                       ThreadLine cannot establish a before/after attention comparison, so this
                       signal means: "entity is currently blocked and attention is CRITICAL."
RISK_DEESCALATED    -- ThreadLine POLICY SIGNAL: entity transitioned to RESOLVED.
                       ThreadLine treats resolution as a risk de-escalation signal by convention.
                       This does NOT guarantee that all organisational risk has been removed.
NEW_DEPENDENCY      -- explicit DEPENDS_ON or BLOCKS relationship observed (never CO_OCCURS_WITH)
DEPENDENCY_EXPANDED -- transitive dependency path observed (depth >= 2 in the explicit graph).
                       This records that such a path EXISTS, not that it newly appeared or expanded.
IMPACT_EXPANDED     -- entity has inbound risk impact associations observed by ImpactAnalysisService.
                       This records that such associations EXIST, not that they grew over time.
ENTITY_BECAME_STALE -- entity newly crossed the staleness threshold

These models are populated exclusively by OrganisationChangeIntelligenceService and must
never be modified by other pipeline stages.
"""

import hashlib
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

class OrganisationChangeType(str, Enum):
    """The category of a detected organisation-wide change.

    Each value represents a distinct, deterministically detectable kind of
    change at the organisation level.
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


class OrganisationChangeSeverity(str, Enum):
    """The severity of a detected organisation-wide change.

    Severity is deterministic -- it is a fixed property of each
    OrganisationChangeType, not a probabilistic score.
    """

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    INFO = "INFO"


# ---------------------------------------------------------------------------
# Deterministic mappings
# ---------------------------------------------------------------------------

#: Maps each OrganisationChangeType to its fixed OrganisationChangeSeverity.
CHANGE_TYPE_SEVERITY: dict[OrganisationChangeType, OrganisationChangeSeverity] = {
    OrganisationChangeType.STATE_OPENED: OrganisationChangeSeverity.INFO,
    OrganisationChangeType.STATE_STARTED: OrganisationChangeSeverity.INFO,
    OrganisationChangeType.STATE_BLOCKED: OrganisationChangeSeverity.HIGH,
    OrganisationChangeType.STATE_RESOLVED: OrganisationChangeSeverity.INFO,
    OrganisationChangeType.STATE_REGRESSED: OrganisationChangeSeverity.HIGH,
    OrganisationChangeType.STATE_REOPENED: OrganisationChangeSeverity.HIGH,
    OrganisationChangeType.REPEATED_UNRESOLVED: OrganisationChangeSeverity.MEDIUM,
    OrganisationChangeType.RISK_ESCALATED: OrganisationChangeSeverity.CRITICAL,
    OrganisationChangeType.RISK_DEESCALATED: OrganisationChangeSeverity.INFO,
    OrganisationChangeType.NEW_DEPENDENCY: OrganisationChangeSeverity.MEDIUM,
    OrganisationChangeType.DEPENDENCY_EXPANDED: OrganisationChangeSeverity.MEDIUM,
    OrganisationChangeType.IMPACT_EXPANDED: OrganisationChangeSeverity.MEDIUM,
    OrganisationChangeType.ENTITY_BECAME_STALE: OrganisationChangeSeverity.HIGH,
}

#: Numeric weight for deterministic ordering. Higher = more severe (sort DESC).
CHANGE_SEVERITY_ORDER: dict[OrganisationChangeSeverity, int] = {
    OrganisationChangeSeverity.CRITICAL: 4,
    OrganisationChangeSeverity.HIGH: 3,
    OrganisationChangeSeverity.MEDIUM: 2,
    OrganisationChangeSeverity.INFO: 1,
}

#: Priority order for change types in deterministic sort. Lower number = higher priority.
CHANGE_TYPE_PRIORITY: dict[OrganisationChangeType, int] = {
    OrganisationChangeType.RISK_ESCALATED: 1,
    OrganisationChangeType.STATE_BLOCKED: 2,
    OrganisationChangeType.STATE_REGRESSED: 3,
    OrganisationChangeType.STATE_REOPENED: 4,
    OrganisationChangeType.ENTITY_BECAME_STALE: 5,
    OrganisationChangeType.REPEATED_UNRESOLVED: 6,
    OrganisationChangeType.IMPACT_EXPANDED: 7,
    OrganisationChangeType.DEPENDENCY_EXPANDED: 8,
    OrganisationChangeType.NEW_DEPENDENCY: 9,
    OrganisationChangeType.RISK_DEESCALATED: 10,
    OrganisationChangeType.STATE_RESOLVED: 11,
    OrganisationChangeType.STATE_STARTED: 12,
    OrganisationChangeType.STATE_OPENED: 13,
}

#: Defines which state transitions are considered regressions.
#: IN_PROGRESS -> BLOCKED: regression (was progressing, now blocked)
#: OPEN -> BLOCKED: regression (was open but now blocked before properly starting)
REGRESSION_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("IN_PROGRESS", "BLOCKED"),
    ("OPEN", "BLOCKED"),
})


def make_change_id(
    entity_id: str,
    change_type: "OrganisationChangeType",
    transition_key: str,
    meeting_id: str,
) -> str:
    """Return a deterministic 16-character change identifier.

    Computes sha256(entity_id:change_type:transition_key:meeting_id)[:16].

    Parameters
    ----------
    entity_id:
        ID of the canonical entity.
    change_type:
        The OrganisationChangeType of this change.
    transition_key:
        A string uniquely describing the specific change within its type
        (e.g. "OPEN:IN_PROGRESS" for a state transition, or a dependency
        pair "src_id:tgt_id:DEPENDS_ON" for a NEW_DEPENDENCY change).
    meeting_id:
        The most relevant meeting ID, or an empty string for entity-level changes.

    Returns
    -------
    str
        A 16-character lowercase hexadecimal string.
    """
    raw = f"{entity_id}:{change_type.value}:{transition_key}:{meeting_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# OrganisationChange -- atomic unit of organisation-wide change intelligence
# ---------------------------------------------------------------------------

class OrganisationChange(BaseModel):
    """A single detected organisation-wide change about a canonical entity.

    OrganisationChange is the atomic output of the Organisation Change
    Intelligence layer. It is always grounded in evidence produced by
    lower-level ThreadLine intelligence layers -- never fabricated.

    Every change is fully deterministic: the same repository state always
    produces the same change_id, same type, same severity, and same ordering.

    A consumer can trace: Change -> Entity -> Meeting -> Evidence -> Intelligence signal.
    """

    change_id: str = Field(
        ...,
        description=(
            "Deterministic 16-character hex identifier. "
            "sha256(entity_id:change_type:transition_key:meeting_id)[:16]. "
            "Identical across repeated calls given the same data."
        ),
    )

    entity_id: str = Field(
        ..., description="ID of the canonical entity this change refers to."
    )

    change_type: OrganisationChangeType = Field(
        ..., description="The category of change detected."
    )

    severity: OrganisationChangeSeverity = Field(
        ...,
        description=(
            "Fixed severity level for this change type. "
            "Determined by CHANGE_TYPE_SEVERITY -- never probabilistic."
        ),
    )

    detected_at: Optional[datetime] = Field(
        default=None,
        description=(
            "Timestamp of the underlying event. For timeline-derived changes: "
            "the meeting_date of the triggering observation. For entity-level "
            "changes: the insight observed_at. None if no temporal evidence exists."
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

    deterministic_sort_key: str = Field(
        ...,
        description=(
            "Pre-computed sort key for stable, reproducible ordering. "
            "Format: severity_order|change_type_priority|detected_at_iso|entity_id|change_id."
        ),
    )


# ---------------------------------------------------------------------------
# OrganisationChangeSummary -- aggregate counts
# ---------------------------------------------------------------------------

class OrganisationChangeSummary(BaseModel):
    """Structured aggregate summary of detected organisation-wide changes.

    All fields are deterministic counts and lists derived from OrganisationChange records.
    NEVER generated by an LLM.
    """

    total_changes: int = Field(
        ..., ge=0, description="Total number of detected changes."
    )

    critical_changes: int = Field(
        ..., ge=0, description="Number of CRITICAL severity changes."
    )

    high_changes: int = Field(
        ..., ge=0, description="Number of HIGH severity changes."
    )

    medium_changes: int = Field(
        ..., ge=0, description="Number of MEDIUM severity changes."
    )

    info_changes: int = Field(
        ..., ge=0, description="Number of INFO severity changes."
    )

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
