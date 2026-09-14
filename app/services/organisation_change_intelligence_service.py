"""Organisation-Wide Change Intelligence Service (Stage 17).

This service is the aggregation layer that consumes existing ThreadLine intelligence
to detect and report organisation-wide changes.

Architecture
------------
OrganisationChangeIntelligenceService is a READ-ONLY COMPOSITION layer.
It consumes existing services without reimplementing their logic:

  InsightService           -> state changes, blocked, resolved, reopen, repeated, stale
  AttentionService         -> risk signal detection (ENTITY_BLOCKED attention reason)
  DependencyGraphService   -> transitive dependency path discovery
  ImpactAnalysisService    -> inbound risk impact association discovery
  EntityRelationshipService -> relationship queries (dependency repository access)

This service does NOT:
  - Reimplement temporal state logic.
  - Reimplement dependency traversal.
  - Reimplement impact calculation.
  - Reimplement attention scoring.
  - Reimplement insight generation.
  - Modify any entity, mention, meeting, insight, or repository state.
  - Call datetime.now() internally. current_time is always passed in by the caller.
  - Use LLMs, embeddings, or probabilistic inference.
  - Fabricate detected_at timestamps. Every timestamp comes from meeting_date or
    insight.observed_at in the underlying repository.

Change Detection Rules
----------------------
R1  STATE_OPENED         -- InsightType.STATE_CHANGED + from=UNKNOWN + to=OPEN
R2  STATE_STARTED        -- InsightType.STATE_CHANGED + to=IN_PROGRESS
R3  STATE_BLOCKED        -- InsightType.ISSUE_BLOCKED
R4  STATE_RESOLVED       -- InsightType.ISSUE_RESOLVED
R5  STATE_REGRESSED      -- InsightType.ISSUE_BLOCKED where from_state in REGRESSION_TRANSITIONS
R6  STATE_REOPENED       -- InsightType.REOPEN_ATTEMPT
R7  REPEATED_UNRESOLVED  -- InsightType.REPEATED_OBSERVATION
R8  RISK_ESCALATED       -- AttentionReason.ENTITY_BLOCKED in attention.reasons.
                           SEMANTICS: This is a CURRENT-STATE SIGNAL, not a proven historical
                           risk transition. The service cannot establish before/after attention
                           history. Evidence is worded conservatively.
R9  RISK_DEESCALATED     -- InsightType.ISSUE_RESOLVED.
                           SEMANTICS: ThreadLine POLICY rule. Resolution is treated as a
                           risk de-escalation signal by convention, not as a guarantee that
                           all organisational risk has been removed.
R10 NEW_DEPENDENCY       -- ExplicitDependency records (DEPENDS_ON or BLOCKS only).
                           detected_at = meeting.meeting_date from the dependency's meeting_id.
                           Leave detected_at unset when no meeting can be resolved.
R11 DEPENDENCY_OBSERVED  -- DependencyGraphService transitive paths (depth >= 2).
                           SEMANTICS: Records that a transitive path EXISTS, not that it
                           recently appeared or expanded. Stored as DEPENDENCY_EXPANDED for
                           API compatibility. Evidence is worded conservatively.
R12 IMPACT_OBSERVED      -- ImpactAnalysisService returns >= 1 impact records.
                           SEMANTICS: Records that impact associations EXIST, not that they
                           grew over time. Stored as IMPACT_EXPANDED for API compatibility.
                           Evidence is worded conservatively.
R13 ENTITY_BECAME_STALE  -- InsightType.STALE_ENTITY

Temporal evidence policy
------------------------
- State/insight-derived changes (R1-R9, R13): detected_at = insight.observed_at
  (which equals meeting_date of the triggering observation).
- NEW_DEPENDENCY (R10): detected_at = meeting.meeting_date, resolved from dep.meeting_id.
  If the meeting cannot be found in the repository, the change is skipped (not emitted).
- DEPENDENCY_EXPANDED/IMPACT_EXPANDED (R11, R12): detected_at = None.
  These represent graph-level topology signals with no single meeting anchor.
  Date filtering (start_date/end_date) will automatically exclude these if filtering is active.
- datetime.now() is NEVER used to represent a historical event's detected_at.

Deduplication
-------------
change_id is deterministic: sha256(entity_id:change_type:transition_key:meeting_id)[:16].
The same underlying evidence always produces the same change_id. A seen_ids set prevents
any duplicate from being emitted within a single get_changes() call.

Change ID key policy per type:
  STATE_OPENED/STARTED        -- transition_key = "{from_state}:{to_state}", meeting_id from insight
  STATE_BLOCKED               -- transition_key = "{from}:BLOCKED", meeting_id from insight
  STATE_REGRESSED             -- transition_key = "REGRESSION:{from}:BLOCKED", meeting_id from insight
  STATE_RESOLVED              -- transition_key = "{from}:RESOLVED", meeting_id from insight
  RISK_DEESCALATED            -- transition_key = "RESOLVED_FROM:{from}", meeting_id from insight
  STATE_REOPENED              -- transition_key = "REOPEN_ATTEMPT", meeting_id from insight
  REPEATED_UNRESOLVED         -- transition_key = "REPEATED:{meeting_id}", meeting_id from insight
  ENTITY_BECAME_STALE         -- transition_key = "STALE", meeting_id from insight
  RISK_ESCALATED              -- transition_key = "ENTITY_BLOCKED", meeting_id from blocking insight
  NEW_DEPENDENCY              -- transition_key = "{src}:{tgt}:{type}", meeting_id = dep.meeting_id
  DEPENDENCY_EXPANDED         -- transition_key = ":".join(entity_path), meeting_id = ""
  IMPACT_EXPANDED             -- transition_key = "impacts:{sorted_sources}", meeting_id = ""

Date filtering
--------------
start_date and end_date filter by detected_at (inclusive).
Graph-level changes (DEPENDENCY_EXPANDED, IMPACT_EXPANDED) have no single date (detected_at=None).
A start_date or end_date filter of any real date will exclude changes that have no temporal evidence.

Deterministic ordering
-----------------------
1. severity DESC (CRITICAL first via CHANGE_SEVERITY_ORDER)
2. change_type priority ASC (lower number = higher priority)
3. detected_at DESC (most recent first, using negative timestamp for sort)
4. entity_id ASC (stable tie-breaker)
5. change_id ASC (final stable tie-breaker)

Failure safety
--------------
If processing one entity fails, that entity is skipped and processing continues
for remaining entities. Errors are logged but not propagated. This ensures one
malformed entity cannot block the entire organisation-wide intelligence run.

CO_OCCURS_WITH constraint
-------------------------
CO_OCCURS_WITH is NEVER promoted to a dependency change.
Only ExplicitDependency records (DEPENDS_ON, BLOCKS) qualify for R10/R11.
CO_OCCURS_WITH is computed on-the-fly by EntityRelationshipService and is never
stored in the dependency repository.

Multiple graph paths
--------------------
Multiple graph paths to the same entity in DEPENDENCY_EXPANDED are deduplicated
using path_id. The same logical path does not generate duplicate changes.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from app.models.insights import InsightType
from app.models.attention import AttentionReason
from app.models.temporal import TemporalState
from app.models.organisation_change import (
    CHANGE_SEVERITY_ORDER,
    CHANGE_TYPE_PRIORITY,
    CHANGE_TYPE_SEVERITY,
    REGRESSION_TRANSITIONS,
    OrganisationChange,
    OrganisationChangeSeverity,
    OrganisationChangeSummary,
    OrganisationChangeType,
    make_change_id,
)
from app.repositories.dependency_repository import AbstractDependencyRepository
from app.repositories.entity_repository import AbstractEntityRepository
from app.repositories.meeting_repository import AbstractMeetingRepository
from app.repositories.mention_repository import AbstractMentionRepository
from app.services.attention_service import AttentionService
from app.services.dependency_graph_service import DependencyGraphService
from app.services.entity_relationship_service import EntityRelationshipService
from app.services.impact_analysis_service import ImpactAnalysisService
from app.services.insight_service import DEFAULT_STALE_THRESHOLD_DAYS, InsightService
from app.services.temporal_state_service import TemporalStateService
from app.temporal.state_interpreter import AbstractStateInterpreter
from app.temporal.transition_policy import AbstractTemporalStatePolicy

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

def _sort_changes(changes: list[OrganisationChange]) -> list[OrganisationChange]:
    """Sort changes with full deterministic ordering.

    Order:
    1. severity DESC (CRITICAL=4 first)
    2. change_type priority ASC (1 = highest priority)
    3. detected_at DESC (most recent first; negate timestamp for DESC)
    4. entity_id ASC
    5. change_id ASC

    This is the single canonical sort function for OrganisationChange lists.
    All sorting in this module goes through this function.
    """
    return sorted(
        changes,
        key=lambda c: (
            -CHANGE_SEVERITY_ORDER[c.severity],
            CHANGE_TYPE_PRIORITY[c.change_type],
            -c.detected_at.timestamp() if c.detected_at else 0.0,
            c.entity_id,
            c.change_id,
        ),
    )


def _build_sort_key(
    severity: OrganisationChangeSeverity,
    change_type: OrganisationChangeType,
    detected_at: Optional[datetime],
    entity_id: str,
    change_id: str,
) -> str:
    """Compute the deterministic sort key string stored on OrganisationChange.

    This pre-computed string mirrors the tuple key used by _sort_changes() so that
    consumers can reproduce the sort order without re-running the function.
    Format: "{severity_order}|{type_priority:02d}|{detected_at_iso}|{entity_id}|{change_id}"
    """
    sev_order = CHANGE_SEVERITY_ORDER[severity]
    type_prio = CHANGE_TYPE_PRIORITY[change_type]
    return (
        f"{sev_order:01d}|{type_prio:02d}|"
        f"{detected_at.isoformat() if detected_at else 'NONE'}|{entity_id}|{change_id}"
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class OrganisationChangeIntelligenceService:
    """Read-only service that detects and aggregates organisation-wide changes.

    OrganisationChangeIntelligenceService consumes existing ThreadLine
    intelligence signals and applies deterministic rules to detect changes
    that deserve organisational attention.

    This service NEVER:
    - Modifies entities, mentions, meetings, insights, or any repository state.
    - Calls datetime.now() internally. current_time is always passed in by callers.
    - Fabricates detected_at timestamps. Every timestamp comes from the repository.
    - Fabricates evidence or invents changes without traceable backing.
    - Uses LLMs, embeddings, or probabilistic inference.
    - Reinvents lower-level state/impact calculations.

    All dependencies are injected via the constructor so this service is fully
    testable without HTTP or real storage.
    """

    def __init__(
        self,
        entity_repo: AbstractEntityRepository,
        mention_repo: AbstractMentionRepository,
        meeting_repo: AbstractMeetingRepository,
        interpreter: AbstractStateInterpreter,
        policy: AbstractTemporalStatePolicy,
        dependency_repo: Optional[AbstractDependencyRepository] = None,
        current_revision_lookup=None,
    ) -> None:
        self._entity_repo = entity_repo
        self._meeting_repo = meeting_repo  # stored for NEW_DEPENDENCY timestamp resolution
        self._dependency_repo = dependency_repo
        # Optional callable mapping meeting_id -> current source revision.  When
        # provided, only current-revision dependencies/mentions shape detected
        # changes; staleness in revision-stamped durable state is disregarded.
        self._current_revision_lookup = current_revision_lookup

        # Compose existing services (read-only, stateless).
        self._insight_service = InsightService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            meeting_repo=meeting_repo,
            interpreter=interpreter,
            policy=policy,
        )
        self._attention_service = AttentionService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            meeting_repo=meeting_repo,
            interpreter=interpreter,
            policy=policy,
        )
        self._temporal_service = TemporalStateService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            meeting_repo=meeting_repo,
            interpreter=interpreter,
            policy=policy,
        )
        relationship_service = EntityRelationshipService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            dependency_repo=dependency_repo,
            current_revision_lookup=current_revision_lookup,
        )

        if dependency_repo is not None:
            self._dependency_graph_service: Optional[DependencyGraphService] = (
                DependencyGraphService(
                    dependency_repo=dependency_repo,
                    entity_repo=entity_repo,
                    current_revision_lookup=current_revision_lookup,
                )
            )
        else:
            self._dependency_graph_service = None

        self._impact_service = ImpactAnalysisService(
            entity_repo=entity_repo,
            relationship_service=relationship_service,
            temporal_service=self._temporal_service,
            insight_service=self._insight_service,
            attention_service=self._attention_service,
            dependency_graph_service=self._dependency_graph_service,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_changes(
        self,
        current_time: Optional[datetime] = None,
        stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        change_type: Optional[OrganisationChangeType] = None,
        severity: Optional[OrganisationChangeSeverity] = None,
        entity_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[OrganisationChange]:
        """Detect and return all organisation-wide changes.

        Iterates all canonical entities (or a single entity if entity_id is
        provided), gathers existing intelligence, applies deterministic change
        detection rules, deduplicates, filters by the specified criteria,
        sorts deterministically, and returns the result list.

        Parameters
        ----------
        current_time:
            Reference datetime for time-sensitive services (stale detection,
            attention scoring). Must be timezone-aware.
            When None, falls back to datetime.now(utc) — this is acceptable at
            the public API boundary because callers (API layer) always pass an
            explicit value. The fallback only affects STALE and ATTENTION
            evaluation, never historical detected_at timestamps.
        stale_threshold_days:
            Days threshold for STALE_ENTITY detection. Default: 30.
        start_date:
            Only return changes with detected_at >= start_date (inclusive).
            Note: graph-level changes (DEPENDENCY_EXPANDED, IMPACT_EXPANDED) have
            no single event timestamp and are handled explicitly by date filters.
        end_date:
            Only return changes with detected_at <= end_date (inclusive).
        change_type:
            Only return changes of this type.
        severity:
            Only return changes at this severity level.
        entity_id:
            Only return changes for this specific entity.
        limit:
            Maximum number of changes to return. None means no limit.

        Returns
        -------
        list[OrganisationChange]
            Deterministically ordered list of detected changes.
        """
        ref_time = current_time if current_time is not None else datetime.now(timezone.utc)

        # Determine which entities to process.
        if entity_id is not None:
            entity_obj = self._entity_repo.get_by_id(entity_id)
            if entity_obj is None:
                return []
            entities_to_process = [entity_obj]
        else:
            entities_to_process = self._entity_repo.list_entities()

        logger.info(
            "OrganisationChangeIntelligenceService: detecting changes for %d entities.",
            len(entities_to_process),
        )

        all_changes: list[OrganisationChange] = []
        seen_change_ids: set[str] = set()

        for entity in entities_to_process:
            try:
                entity_changes = self._detect_entity_changes(
                    entity_id=entity.entity_id,
                    current_time=ref_time,
                    stale_threshold_days=stale_threshold_days,
                )
                for change in entity_changes:
                    if change.change_id not in seen_change_ids:
                        seen_change_ids.add(change.change_id)
                        all_changes.append(change)
            except Exception:
                logger.exception(
                    "OrganisationChangeIntelligenceService: error processing "
                    "entity %s, skipping.",
                    entity.entity_id,
                )

        logger.info(
            "OrganisationChangeIntelligenceService: %d raw changes before filtering.",
            len(all_changes),
        )

        # --- Apply filters ---
        filtered = all_changes

        if start_date is not None:
            sd = start_date
            if sd.tzinfo is None:
                sd = sd.replace(tzinfo=timezone.utc)
            filtered = [c for c in filtered if c.detected_at is not None and c.detected_at >= sd]

        if end_date is not None:
            ed = end_date
            if ed.tzinfo is None:
                ed = ed.replace(tzinfo=timezone.utc)
            filtered = [c for c in filtered if c.detected_at is not None and c.detected_at <= ed]

        if change_type is not None:
            filtered = [c for c in filtered if c.change_type == change_type]

        if severity is not None:
            filtered = [c for c in filtered if c.severity == severity]

        # Sort deterministically.
        filtered = _sort_changes(filtered)

        # Apply limit.
        if limit is not None and limit > 0:
            filtered = filtered[:limit]

        logger.info(
            "OrganisationChangeIntelligenceService: %d changes returned after filtering.",
            len(filtered),
        )

        return filtered

    def get_summary(
        self,
        current_time: Optional[datetime] = None,
        stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> OrganisationChangeSummary:
        """Compute a structured aggregate summary of organisation-wide changes.

        Returns a deterministic, structured OrganisationChangeSummary.
        All counts are derived from OrganisationChange records; NEVER LLM-generated.

        Parameters
        ----------
        current_time:
            Reference datetime for time-sensitive services.
        stale_threshold_days:
            Days threshold for STALE_ENTITY detection.
        start_date:
            Window start for filtering changes.
        end_date:
            Window end for filtering changes.

        Returns
        -------
        OrganisationChangeSummary
        """
        changes = self.get_changes(
            current_time=current_time,
            stale_threshold_days=stale_threshold_days,
            start_date=start_date,
            end_date=end_date,
        )

        critical = sum(1 for c in changes if c.severity == OrganisationChangeSeverity.CRITICAL)
        high = sum(1 for c in changes if c.severity == OrganisationChangeSeverity.HIGH)
        medium = sum(1 for c in changes if c.severity == OrganisationChangeSeverity.MEDIUM)
        info = sum(1 for c in changes if c.severity == OrganisationChangeSeverity.INFO)

        newly_blocked = sorted({
            c.entity_id
            for c in changes
            if c.change_type == OrganisationChangeType.STATE_BLOCKED
        })
        newly_resolved = sorted({
            c.entity_id
            for c in changes
            if c.change_type == OrganisationChangeType.STATE_RESOLVED
        })
        regressed = sorted({
            c.entity_id
            for c in changes
            if c.change_type == OrganisationChangeType.STATE_REGRESSED
        })
        reopened = sorted({
            c.entity_id
            for c in changes
            if c.change_type == OrganisationChangeType.STATE_REOPENED
        })
        stale = sorted({
            c.entity_id
            for c in changes
            if c.change_type == OrganisationChangeType.ENTITY_BECAME_STALE
        })
        new_dep_count = sum(
            1 for c in changes if c.change_type == OrganisationChangeType.NEW_DEPENDENCY
        )
        expanded_impact_count = sum(
            1 for c in changes if c.change_type == OrganisationChangeType.IMPACT_EXPANDED
        )

        # Group changes by entity. Values are ordered lists of change_type strings.
        changes_by_entity: dict[str, list[str]] = {}
        for c in changes:
            if c.entity_id not in changes_by_entity:
                changes_by_entity[c.entity_id] = []
            changes_by_entity[c.entity_id].append(c.change_type.value)

        return OrganisationChangeSummary(
            total_changes=len(changes),
            critical_changes=critical,
            high_changes=high,
            medium_changes=medium,
            info_changes=info,
            newly_blocked_entities=newly_blocked,
            newly_resolved_entities=newly_resolved,
            regressed_entities=regressed,
            reopened_entities=reopened,
            new_dependency_count=new_dep_count,
            expanded_impact_count=expanded_impact_count,
            stale_entities=stale,
            changes_by_entity=changes_by_entity,
        )

    # ------------------------------------------------------------------
    # Internal per-entity change detection
    # ------------------------------------------------------------------

    def _detect_entity_changes(
        self,
        entity_id: str,
        current_time: datetime,
        stale_threshold_days: int,
    ) -> list[OrganisationChange]:
        """Detect all organisation-wide changes for a single entity.

        Applies detection rules R1-R13. Returns a list of OrganisationChange
        records. Duplicates are prevented via deterministic change_id generation
        and a local seen_ids set.

        This method never raises — all exceptions are caught, logged, and the
        entity is skipped. This preserves failure isolation: one bad entity
        cannot block the entire intelligence run.
        """
        changes: list[OrganisationChange] = []
        seen_ids: set[str] = set()

        def _add(c: OrganisationChange) -> None:
            """Add change only if not already seen (deduplication guard)."""
            if c.change_id not in seen_ids:
                seen_ids.add(c.change_id)
                changes.append(c)

        # Fetch intelligence signals (read-only).
        insights = self._insight_service.get_entity_insights(
            entity_id=entity_id,
            current_time=current_time,
            stale_threshold_days=stale_threshold_days,
        )
        attention = self._attention_service.get_entity_attention(
            entity_id=entity_id,
            current_time=current_time,
            stale_threshold_days=stale_threshold_days,
        )

        # R1-R7, R9, R13: insight-derived changes.
        for insight in insights:
            self._apply_insight_rules(entity_id, insight, _add)

        # R8: RISK_ESCALATED (current-state signal: entity is blocked, attention is CRITICAL).
        if attention is not None and AttentionReason.ENTITY_BLOCKED in attention.reasons:
            # Find the blocking insight to anchor the timestamp.
            blocking_insights = [
                i for i in insights if i.insight_type == InsightType.ISSUE_BLOCKED
            ]
            if blocking_insights:
                ref_insight = blocking_insights[0]
                meeting_id = ref_insight.related_meeting_id or ""
                cid = make_change_id(
                    entity_id,
                    OrganisationChangeType.RISK_ESCALATED,
                    "ENTITY_BLOCKED",
                    meeting_id,
                )
                severity = CHANGE_TYPE_SEVERITY[OrganisationChangeType.RISK_ESCALATED]
                sort_key = _build_sort_key(
                    severity,
                    OrganisationChangeType.RISK_ESCALATED,
                    ref_insight.observed_at,
                    entity_id,
                    cid,
                )
                _add(OrganisationChange(
                    change_id=cid,
                    entity_id=entity_id,
                    change_type=OrganisationChangeType.RISK_ESCALATED,
                    severity=severity,
                    detected_at=ref_insight.observed_at,
                    meeting_id=ref_insight.related_meeting_id,
                    insight_id=ref_insight.insight_id,
                    evidence=(
                        f"Risk signal: entity is currently in BLOCKED state with CRITICAL "
                        f"attention (score: {attention.score}). "
                        f"This is a current-state signal, not a proven historical risk "
                        f"escalation. Evidence: {ref_insight.evidence}"
                    ),
                    deterministic_sort_key=sort_key,
                ))

        # R10: NEW_DEPENDENCY.
        self._apply_dependency_rules(entity_id, _add)

        # R11: DEPENDENCY_EXPANDED (topology observation).
        self._apply_dependency_expansion_rules(entity_id, _add)

        # R12: IMPACT_EXPANDED (impact association observation).
        self._apply_impact_rules(entity_id, current_time, _add)

        return changes

    def _apply_insight_rules(
        self,
        entity_id: str,
        insight,
        add_fn,
    ) -> None:
        """Apply rules R1-R7, R9, R13 based on a single EntityInsight.

        CO_OCCURS_WITH is never involved here -- insights are derived from
        temporal state and organisational memory, not raw co-occurrence.
        """
        itype = insight.insight_type
        meeting_id = insight.related_meeting_id or ""
        observed_at = insight.observed_at

        def _build(
            change_type: OrganisationChangeType,
            transition_key: str,
            evidence: str,
            previous_state: Optional[str] = None,
            current_state_val: Optional[str] = None,
        ) -> OrganisationChange:
            """Construct an OrganisationChange from an insight-derived rule."""
            cid = make_change_id(entity_id, change_type, transition_key, meeting_id)
            severity = CHANGE_TYPE_SEVERITY[change_type]
            sort_key = _build_sort_key(severity, change_type, observed_at, entity_id, cid)
            return OrganisationChange(
                change_id=cid,
                entity_id=entity_id,
                change_type=change_type,
                severity=severity,
                detected_at=observed_at,
                meeting_id=insight.related_meeting_id,
                insight_id=insight.insight_id,
                evidence=evidence,
                previous_state=previous_state,
                current_state=current_state_val,
                deterministic_sort_key=sort_key,
            )

        if itype == InsightType.STATE_CHANGED:
            # R1: STATE_OPENED (UNKNOWN -> OPEN)
            # R2: STATE_STARTED (any -> IN_PROGRESS)
            from_state, to_state = self._parse_state_transition(insight.description)
            if from_state and to_state:
                transition_key = f"{from_state}:{to_state}"
                if to_state == "OPEN" and from_state == "UNKNOWN":
                    add_fn(_build(
                        OrganisationChangeType.STATE_OPENED,
                        transition_key,
                        f"Entity first observed in OPEN state. {insight.evidence}",
                        previous_state=from_state,
                        current_state_val=to_state,
                    ))
                elif to_state == "IN_PROGRESS":
                    add_fn(_build(
                        OrganisationChangeType.STATE_STARTED,
                        transition_key,
                        f"Entity transitioned to IN_PROGRESS. {insight.evidence}",
                        previous_state=from_state,
                        current_state_val=to_state,
                    ))

        elif itype == InsightType.ISSUE_BLOCKED:
            # R3: STATE_BLOCKED
            from_state, _ = self._parse_state_transition(insight.description)
            transition_key = f"{from_state or 'UNKNOWN'}:BLOCKED"
            add_fn(_build(
                OrganisationChangeType.STATE_BLOCKED,
                transition_key,
                f"Entity entered BLOCKED state. {insight.evidence}",
                previous_state=from_state,
                current_state_val="BLOCKED",
            ))
            # R5: STATE_REGRESSED (when from_state indicates regression)
            if from_state and (from_state, "BLOCKED") in REGRESSION_TRANSITIONS:
                reg_key = f"REGRESSION:{from_state}:BLOCKED"
                add_fn(_build(
                    OrganisationChangeType.STATE_REGRESSED,
                    reg_key,
                    (
                        f"Entity regressed from {from_state} to BLOCKED. "
                        f"{insight.evidence}"
                    ),
                    previous_state=from_state,
                    current_state_val="BLOCKED",
                ))

        elif itype == InsightType.ISSUE_RESOLVED:
            # R4: STATE_RESOLVED + R9: RISK_DEESCALATED
            from_state, _ = self._parse_state_transition(insight.description)
            transition_key = f"{from_state or 'UNKNOWN'}:RESOLVED"
            add_fn(_build(
                OrganisationChangeType.STATE_RESOLVED,
                transition_key,
                f"Entity transitioned to RESOLVED state. {insight.evidence}",
                previous_state=from_state,
                current_state_val="RESOLVED",
            ))
            # R9: RISK_DEESCALATED — ThreadLine policy: resolution is treated as
            # risk de-escalation signal. Does not guarantee all risk is gone.
            deesc_key = f"RESOLVED_FROM:{from_state or 'UNKNOWN'}"
            add_fn(_build(
                OrganisationChangeType.RISK_DEESCALATED,
                deesc_key,
                (
                    f"Risk signal de-escalated: entity transitioned to RESOLVED. "
                    f"ThreadLine policy treats resolution as a risk de-escalation signal. "
                    f"This does not guarantee all organisational risk has been removed. "
                    f"{insight.evidence}"
                ),
                previous_state=from_state,
                current_state_val="RESOLVED",
            ))

        elif itype == InsightType.REOPEN_ATTEMPT:
            # R6: STATE_REOPENED
            add_fn(_build(
                OrganisationChangeType.STATE_REOPENED,
                "REOPEN_ATTEMPT",
                f"Reopen attempt detected on RESOLVED entity. {insight.evidence}",
                previous_state="RESOLVED",
                current_state_val="RESOLVED",
            ))

        elif itype == InsightType.REPEATED_OBSERVATION:
            # R7: REPEATED_UNRESOLVED
            add_fn(_build(
                OrganisationChangeType.REPEATED_UNRESOLVED,
                f"REPEATED:{meeting_id}",
                f"Entity observed repeatedly without state progress. {insight.evidence}",
            ))

        elif itype == InsightType.STALE_ENTITY:
            # R13: ENTITY_BECAME_STALE
            add_fn(_build(
                OrganisationChangeType.ENTITY_BECAME_STALE,
                "STALE",
                f"Entity crossed the staleness threshold. {insight.evidence}",
            ))

        # InsightType.UNKNOWN_STATE -- not mapped to an org change (not a material change).

    def _apply_dependency_rules(
        self,
        entity_id: str,
        add_fn,
    ) -> None:
        """Apply rule R10: NEW_DEPENDENCY.

        Uses the dependency repository directly to find explicit DEPENDS_ON and
        BLOCKS records where entity_id is the source.

        Temporal evidence policy for NEW_DEPENDENCY:
        - Look up dep.meeting_id in the meeting repository.
        - If the meeting is found: detected_at = meeting.meeting_date.
        - If the meeting is NOT found: SKIP the change (do not fabricate a timestamp).

        CO_OCCURS_WITH is NEVER included -- only ExplicitDependency records qualify
        (they are always DEPENDS_ON or BLOCKS by construction).
        """
        if self._dependency_repo is None:
            return

        deps = self._dependency_repo.list_current_by_source_entity_id(
            entity_id, self._current_revision_lookup
        )
        for dep in deps:
            # Skip if the target entity does not exist.
            if self._entity_repo.get_by_id(dep.target_entity_id) is None:
                continue

            # Resolve detected_at from the actual meeting record.
            detected_at = None
            try:
                meeting = self._meeting_repo.get_by_id(dep.meeting_id)
                if meeting is not None:
                    detected_at = meeting.meeting_date
            except Exception:
                pass

            if detected_at is None:
                logger.debug(
                    "OrganisationChangeIntelligenceService: meeting %s not found "
                    "for dependency %s; skipping NEW_DEPENDENCY.",
                    dep.meeting_id,
                    dep.dependency_id,
                )
                continue

            transition_key = (
                f"{dep.source_entity_id}:{dep.target_entity_id}:{dep.relationship_type.value}"
            )
            cid = make_change_id(
                entity_id,
                OrganisationChangeType.NEW_DEPENDENCY,
                transition_key,
                dep.meeting_id,
            )
            severity = CHANGE_TYPE_SEVERITY[OrganisationChangeType.NEW_DEPENDENCY]
            sort_key = _build_sort_key(
                severity,
                OrganisationChangeType.NEW_DEPENDENCY,
                detected_at,
                entity_id,
                cid,
            )
            add_fn(OrganisationChange(
                change_id=cid,
                entity_id=entity_id,
                change_type=OrganisationChangeType.NEW_DEPENDENCY,
                severity=severity,
                detected_at=detected_at,
                meeting_id=dep.meeting_id,
                mention_id=dep.mention_id,
                source_text=dep.source_text,
                dependency_path=[entity_id, dep.target_entity_id],
                related_entity_ids=[dep.target_entity_id],
                evidence=(
                    f"Explicit {dep.relationship_type.value} relationship observed: "
                    f"entity {entity_id} to entity {dep.target_entity_id}. "
                    f"Evidence: {dep.source_text}"
                ),
                deterministic_sort_key=sort_key,
            ))

    def _apply_dependency_expansion_rules(
        self,
        entity_id: str,
        add_fn,
    ) -> None:
        """Apply rule R11: DEPENDENCY_EXPANDED (transitive dependency path observation).

        Uses DependencyGraphService to find transitive (depth >= 2) dependency paths.
        Each unique path produces one change record.

        SEMANTICS: This records that a transitive path EXISTS in the dependency graph.
        It does NOT claim the path recently appeared or expanded. The name
        DEPENDENCY_EXPANDED is preserved for API compatibility.

        CO_OCCURS_WITH is excluded by the DependencyGraphService (which only traverses
        ExplicitDependency records).

        Multiple graph paths to the same entity are deduplicated using path_id.
        """
        if self._dependency_graph_service is None:
            return

        try:
            graph = self._dependency_graph_service.build_dependency_graph(entity_id)
        except Exception:
            logger.debug(
                "OrganisationChangeIntelligenceService: dependency graph failed "
                "for entity %s, skipping.",
                entity_id,
            )
            return

        seen_path_ids: set[str] = set()
        for path in graph.transitive_dependencies:
            if path.path_id in seen_path_ids:
                continue
            seen_path_ids.add(path.path_id)

            path_key = ":".join(path.entity_path)
            cid = make_change_id(
                entity_id,
                OrganisationChangeType.DEPENDENCY_EXPANDED,
                path_key,
                "",
            )
            severity = CHANGE_TYPE_SEVERITY[OrganisationChangeType.DEPENDENCY_EXPANDED]
            sort_key = _build_sort_key(
                severity,
                OrganisationChangeType.DEPENDENCY_EXPANDED,
                None,
                entity_id,
                cid,
            )
            related = sorted({e for e in path.entity_path if e != entity_id})
            add_fn(OrganisationChange(
                change_id=cid,
                entity_id=entity_id,
                change_type=OrganisationChangeType.DEPENDENCY_EXPANDED,
                severity=severity,
                detected_at=None,
                meeting_id=None,
                dependency_path=path.entity_path,
                related_entity_ids=related,
                evidence=(
                    f"Transitive dependency path observed (depth={path.depth}): "
                    f"{' -> '.join(path.entity_path)}. "
                    "This records that such a path exists in the explicit dependency graph, "
                    "not that it recently appeared or expanded. "
                    "These are connected entities, not a causal claim."
                ),
                deterministic_sort_key=sort_key,
            ))

    def _apply_impact_rules(
        self,
        entity_id: str,
        current_time: datetime,
        add_fn,
    ) -> None:
        """Apply rule R12: IMPACT_EXPANDED (impact association observation).

        Uses ImpactAnalysisService to detect inbound risk impact associations.
        Only emits a change if at least one impact exists.

        SEMANTICS: This records that impact associations EXIST for this entity.
        It does NOT claim impact grew or expanded over time. The name IMPACT_EXPANDED
        is preserved for API compatibility.

        Change ID key: "impacts:{sorted_source_ids}" — stable across repeated calls
        for the same set of impact sources. If the impact source set changes, the
        change_id changes accordingly (which is correct: a different set of associations
        is a different logical signal).

        Only one IMPACT_EXPANDED change per entity is emitted per call.
        """
        try:
            impacts = self._impact_service.get_entity_impacts(
                entity_id=entity_id,
                current_time=current_time,
            )
        except Exception:
            logger.debug(
                "OrganisationChangeIntelligenceService: impact analysis failed "
                "for entity %s, skipping.",
                entity_id,
            )
            return

        if not impacts:
            return

        impact_sources = sorted({imp.source_entity_id for imp in impacts})
        # Use sorted source IDs as the stable key — more stable than count.
        sources_key = ",".join(impact_sources)
        cid = make_change_id(
            entity_id,
            OrganisationChangeType.IMPACT_EXPANDED,
            f"impacts:{sources_key}",
            "",
        )
        severity = CHANGE_TYPE_SEVERITY[OrganisationChangeType.IMPACT_EXPANDED]
        sort_key = _build_sort_key(
            severity,
            OrganisationChangeType.IMPACT_EXPANDED,
            None,
            entity_id,
            cid,
        )
        add_fn(OrganisationChange(
            change_id=cid,
            entity_id=entity_id,
            change_type=OrganisationChangeType.IMPACT_EXPANDED,
            severity=severity,
            detected_at=None,
            meeting_id=None,
            impact_count=len(impacts),
            related_entity_ids=impact_sources,
            evidence=(
                f"Inbound risk impact associations observed for entity "
                f"({len(impacts)} association(s)) from: {', '.join(impact_sources)}. "
                "This records that such associations exist, not that they grew over time. "
                "These are connected-entity risk signals, not causal claims."
            ),
            deterministic_sort_key=sort_key,
        ))

    @staticmethod
    def _parse_state_transition(description: str) -> tuple[Optional[str], Optional[str]]:
        """Extract (from_state, to_state) from an InsightService description.

        InsightService generates descriptions like:
          "The entity transitioned from OPEN to IN_PROGRESS."
          "The entity transitioned from IN_PROGRESS to BLOCKED."
          "The entity transitioned from UNKNOWN to BLOCKED."

        Returns (None, None) if parsing fails or the states are not valid TemporalState values.
        """
        try:
            if "transitioned from" in description and " to " in description:
                after_from = description.split("transitioned from")[1].strip()
                parts = after_from.split(" to ")
                if len(parts) >= 2:
                    from_state = parts[0].strip().rstrip(".")
                    to_state = parts[1].strip().rstrip(".")
                    valid_states = {s.value for s in TemporalState}
                    if from_state in valid_states and to_state in valid_states:
                        return from_state, to_state
        except Exception:
            pass
        return None, None
