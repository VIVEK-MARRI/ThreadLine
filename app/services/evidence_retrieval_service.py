"""Evidence Retrieval Service (Stage 18).

Composes intelligence signals from all underlying ThreadLine services (Stages 1-17)
into a unified list of EvidenceItem objects based on the QueryIntent.

Design principles
-----------------
- Read-only: never modifies any state.
- Deterministic: fetches data deterministically, assigns deterministic evidence_ids.
- Bounded: does not retrieve more items than requested if possible (though context
  builder does the strict final bounding).
- Intent-driven: only calls the services relevant to the specific intent to save work.
- Type priority: assigns a type_priority to each EvidenceItem based on how relevant
  that EvidenceType is for the given QueryIntent.
- Formatting: converts domain models (EntityInsight, EntityAction, etc.) into
  human-readable strings for the EvidenceItem.summary field.
"""

import logging
from datetime import datetime
from typing import Optional

from app.models.entity import CanonicalEntity
from app.models.natural_language import (
    EvidenceItem,
    EvidenceType,
    QueryIntent,
    _make_evidence_id,
)
from app.models.portfolio import PORTFOLIO_RISK_LEVEL_ORDER, PortfolioRiskLevel
from app.repositories.entity_repository import AbstractEntityRepository
from app.repositories.meeting_repository import AbstractMeetingRepository
from app.services.action_recommendation_service import ActionRecommendationService
from app.services.attention_service import AttentionService
from app.services.dependency_graph_service import DependencyGraphService
from app.services.entity_relationship_service import EntityRelationshipService
from app.services.impact_analysis_service import ImpactAnalysisService
from app.services.insight_service import InsightService
from app.services.organisation_change_intelligence_service import (
    OrganisationChangeIntelligenceService,
)
from app.services.organisational_memory_service import OrganisationalMemoryService
from app.services.portfolio_intelligence_service import PortfolioIntelligenceService
from app.services.unified_timeline_service import UnifiedTimelineService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Priority weights mapping (Intent -> EvidenceType -> Priority)
# Lower is better. Unlisted types get 99.
# ---------------------------------------------------------------------------

_INTENT_PRIORITIES = {
    QueryIntent.ENTITY_STATUS: {
        EvidenceType.STATE: 1,
        EvidenceType.ATTENTION: 2,
        EvidenceType.INSIGHT: 3,
        EvidenceType.MEMORY_FACT: 4,
        EvidenceType.STATE_TRANSITION: 5,
    },
    QueryIntent.ENTITY_HISTORY: {
        EvidenceType.STATE_TRANSITION: 1,
        EvidenceType.MEMORY_FACT: 2,
        EvidenceType.OBSERVATION: 3,
    },
    QueryIntent.ENTITY_RISKS: {
        EvidenceType.ATTENTION: 1,
        EvidenceType.IMPACT: 2,
        EvidenceType.INSIGHT: 3,
        EvidenceType.DEPENDENCY: 4,
    },
    QueryIntent.ENTITY_DEPENDENCIES: {
        EvidenceType.DEPENDENCY_PATH: 1,
        EvidenceType.DEPENDENCY: 2,
        EvidenceType.RELATIONSHIP: 3,
    },
    QueryIntent.ENTITY_IMPACTS: {
        EvidenceType.IMPACT: 1,
        EvidenceType.DEPENDENCY_PATH: 2,
    },
    QueryIntent.ENTITY_ACTIONS: {
        EvidenceType.ACTION: 1,
        EvidenceType.INSIGHT: 2,
        EvidenceType.ATTENTION: 3,
    },
    QueryIntent.ENTITY_CHANGES: {
        EvidenceType.ORGANISATION_CHANGE: 1,
        EvidenceType.STATE_TRANSITION: 2,
    },
    QueryIntent.ORGANISATION_PRIORITIES: {
        EvidenceType.ATTENTION: 1,
        EvidenceType.ACTION: 2,
        EvidenceType.INSIGHT: 3,
    },
    QueryIntent.ORGANISATION_CHANGES: {
        EvidenceType.ORGANISATION_CHANGE: 1,
    },
    QueryIntent.ORGANISATION_RISKS: {
        EvidenceType.ATTENTION: 1,
        EvidenceType.IMPACT: 2,
        EvidenceType.INSIGHT: 3,
    },
}


def _get_priority(intent: QueryIntent, ev_type: EvidenceType) -> int:
    return _INTENT_PRIORITIES.get(intent, {}).get(ev_type, 99)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class EvidenceRetrievalService:
    """Composes underlying intelligence signals into standard EvidenceItems."""

    def __init__(
        self,
        entity_repo: AbstractEntityRepository,
        meeting_repo: AbstractMeetingRepository,
        timeline_svc: UnifiedTimelineService,
        memory_svc: OrganisationalMemoryService,
        insight_svc: InsightService,
        attention_svc: AttentionService,
        action_svc: ActionRecommendationService,
        dependency_graph_svc: DependencyGraphService,
        impact_svc: ImpactAnalysisService,
        org_change_svc: OrganisationChangeIntelligenceService,
        portfolio_svc: PortfolioIntelligenceService,
        relationship_svc: EntityRelationshipService,
    ) -> None:
        self._entity_repo = entity_repo
        self._meeting_repo = meeting_repo
        self._timeline_svc = timeline_svc
        self._memory_svc = memory_svc
        self._insight_svc = insight_svc
        self._attention_svc = attention_svc
        self._action_svc = action_svc
        self._dependency_graph_svc = dependency_graph_svc
        self._impact_svc = impact_svc
        self._org_change_svc = org_change_svc
        self._portfolio_svc = portfolio_svc
        self._relationship_svc = relationship_svc

    def retrieve_evidence(
        self,
        intent: QueryIntent,
        entity_id: Optional[str],
        current_time: datetime,
        max_items: int,
        include_source_text: bool,
    ) -> list[EvidenceItem]:
        """Fetch and convert intelligence into EvidenceItems for the given intent.

        Parameters
        ----------
        intent: The classified QueryIntent.
        entity_id: Target entity_id if applicable, else None.
        current_time: Reference time for fetching stale data.
        max_items: Hint for limiting large collections early.
        include_source_text: If True, include transcript source texts.

        Returns
        -------
        Unbounded, un-deduplicated list of EvidenceItem.
        (EvidenceContextBuilder handles exact bounds and deduplication.)
        """
        items: list[EvidenceItem] = []

        if intent == QueryIntent.UNKNOWN:
            return items

        # Always add the entity definition itself if an entity was requested
        if entity_id:
            entity = self._entity_repo.get_by_id(entity_id)
            if entity:
                items.append(
                    self._build_entity_evidence(intent, entity)
                )

        if entity_id:
            # Entity-specific intents
            if intent in [QueryIntent.ENTITY_STATUS, QueryIntent.ENTITY_HISTORY]:
                items.extend(self._fetch_timeline_evidence(intent, entity_id, current_time, include_source_text))
                items.extend(self._fetch_memory_evidence(intent, entity_id))
                if intent == QueryIntent.ENTITY_STATUS:
                    items.extend(self._fetch_attention_evidence(intent, entity_id, current_time))
                    items.extend(self._fetch_insight_evidence(intent, entity_id, current_time, include_source_text))

            elif intent == QueryIntent.ENTITY_RISKS:
                items.extend(self._fetch_attention_evidence(intent, entity_id, current_time))
                items.extend(self._fetch_insight_evidence(intent, entity_id, current_time, include_source_text))
                items.extend(self._fetch_impact_evidence(intent, entity_id, current_time))
                items.extend(self._fetch_timeline_evidence(intent, entity_id, current_time, include_source_text))

            elif intent == QueryIntent.ENTITY_DEPENDENCIES:
                items.extend(self._fetch_dependency_evidence(intent, entity_id))
                items.extend(self._fetch_relationship_evidence(intent, entity_id, include_source_text))

            elif intent == QueryIntent.ENTITY_IMPACTS:
                items.extend(self._fetch_impact_evidence(intent, entity_id, current_time, multi_hop=True))

            elif intent == QueryIntent.ENTITY_ACTIONS:
                items.extend(self._fetch_action_evidence(intent, entity_id, current_time))
                items.extend(self._fetch_attention_evidence(intent, entity_id, current_time))
                items.extend(self._fetch_insight_evidence(intent, entity_id, current_time, include_source_text))

            elif intent == QueryIntent.ENTITY_CHANGES:
                items.extend(self._fetch_org_change_evidence(intent, current_time, entity_id, max_items, include_source_text))
        else:
            # Organisation-wide intents
            if intent == QueryIntent.ORGANISATION_CHANGES:
                items.extend(self._fetch_org_change_evidence(intent, current_time, None, max_items, include_source_text))

            elif intent == QueryIntent.ORGANISATION_PRIORITIES:
                items.extend(self._fetch_portfolio_evidence(intent, current_time))
                items.extend(self._fetch_top_attention_evidence(intent, current_time, max_items))

            elif intent == QueryIntent.ORGANISATION_RISKS:
                items.extend(self._fetch_portfolio_evidence(intent, current_time, risk_only=True))
                items.extend(self._fetch_top_attention_evidence(intent, current_time, max_items))

        return items

    def build_semantic_corpus(self, current_time: datetime, include_source_text: bool = True) -> list[EvidenceItem]:
        """Build the authoritative, read-only evidence corpus for semantic lookup."""
        items: list[EvidenceItem] = []
        entities = self._entity_repo.list_entities()
        entity_intents = [
            QueryIntent.ENTITY_STATUS, QueryIntent.ENTITY_HISTORY,
            QueryIntent.ENTITY_RISKS, QueryIntent.ENTITY_DEPENDENCIES,
            QueryIntent.ENTITY_IMPACTS, QueryIntent.ENTITY_ACTIONS,
            QueryIntent.ENTITY_CHANGES,
        ]
        for entity in entities:
            for intent in entity_intents:
                items.extend(self.retrieve_evidence(intent, entity.entity_id, current_time, 1000, include_source_text))
        for intent in (
            QueryIntent.ORGANISATION_CHANGES,
            QueryIntent.ORGANISATION_PRIORITIES,
            QueryIntent.ORGANISATION_RISKS,
        ):
            items.extend(self.retrieve_evidence(intent, None, current_time, 1000, include_source_text))
        unique = {item.evidence_id: item for item in items}
        return list(unique.values())

    # -----------------------------------------------------------------------
    # Builders
    # -----------------------------------------------------------------------

    def _build_entity_evidence(self, intent: QueryIntent, entity: CanonicalEntity) -> EvidenceItem:
        ev_type = EvidenceType.ENTITY
        summary = f"Entity '{entity.canonical_name}' is a {entity.entity_type.value}."
        if entity.aliases:
            summary += f" Known aliases: {', '.join(entity.aliases)}."
        
        return EvidenceItem(
            evidence_id=_make_evidence_id(ev_type, entity.entity_id, None, "entity"),
            evidence_type=ev_type,
            entity_id=entity.entity_id,
            summary=summary,
            timestamp=entity.created_at,
            type_priority=_get_priority(intent, ev_type),
            severity_weight=0,
            source_reference="Entity Database",
            metadata={"entity_type": entity.entity_type.value},
        )

    # -----------------------------------------------------------------------
    # Fetchers
    # -----------------------------------------------------------------------

    def _fetch_timeline_evidence(
        self, intent: QueryIntent, entity_id: str, current_time: datetime, include_source_text: bool
    ) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        timeline = self._timeline_svc.get_unified_timeline(entity_id, current_time)
        
        if not timeline:
            return items

        for ev in timeline.events:
            if ev.event_type == "OBSERVATION":
                ev_type = EvidenceType.OBSERVATION
                weight = 1
                meta = {"meeting_id": ev.related_meeting_id}
            elif ev.event_type == "STATE_TRANSITION":
                ev_type = EvidenceType.STATE_TRANSITION
                weight = 3 if ev.event_metadata.get("to_state") in ("BLOCKED", "RESOLVED") else 2
                meta = ev.event_metadata
            else:
                continue
                
            source_text = None
            if include_source_text and ev.event_metadata.get("source_text"):
                source_text = ev.event_metadata["source_text"]

            # Format the summary
            if ev.event_type == "STATE_TRANSITION":
                fr = ev.event_metadata.get("from_state", "UNKNOWN")
                to = ev.event_metadata.get("to_state", "UNKNOWN")
                summary = f"State changed from {fr} to {to}. {ev.description}"
            else:
                summary = ev.title
                if ev.description:
                    summary += f": {ev.description}"

            meeting = self._meeting_repo.get_by_id(ev.related_meeting_id) if ev.related_meeting_id else None
            ref = f"Meeting: {meeting.title}" if meeting else "Timeline Event"
                
            items.append(EvidenceItem(
                evidence_id=_make_evidence_id(ev_type, entity_id, ev.related_meeting_id, ev.event_id),
                evidence_type=ev_type,
                entity_id=entity_id,
                meeting_id=ev.related_meeting_id,
                source_text=source_text,
                timestamp=ev.occurred_at,
                summary=summary,
                severity_weight=weight,
                type_priority=_get_priority(intent, ev_type),
                metadata=meta,
                source_reference=ref,
            ))
            
        return items

    def _fetch_memory_evidence(self, intent: QueryIntent, entity_id: str) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        memory = self._memory_svc.get_entity_memory(entity_id)
        if not memory:
            return items

        for fact in memory.facts:
            ev_type = EvidenceType.MEMORY_FACT
            if fact.fact_type == "CURRENT_STATE":
                ev_type = EvidenceType.STATE
                
            summary = f"{fact.fact_type}: {fact.value}"
            if fact.detail:
                summary += f" ({fact.detail})"
                
            ref = "Organisational Memory"
            if fact.source_meeting_id:
                meeting = self._meeting_repo.get_by_id(fact.source_meeting_id)
                if meeting:
                    ref = f"Meeting: {meeting.title}"

            items.append(EvidenceItem(
                evidence_id=_make_evidence_id(ev_type, entity_id, fact.source_meeting_id, fact.fact_type),
                evidence_type=ev_type,
                entity_id=entity_id,
                meeting_id=fact.source_meeting_id,
                mention_id=fact.source_mention_id,
                timestamp=fact.observed_at,
                summary=summary,
                severity_weight=2 if fact.fact_type == "STATE_TRANSITION" else 1,
                type_priority=_get_priority(intent, ev_type),
                source_reference=ref,
            ))
            
        return items

    def _fetch_attention_evidence(
        self, intent: QueryIntent, entity_id: str, current_time: datetime
    ) -> list[EvidenceItem]:
        att = self._attention_svc.get_entity_attention(entity_id, current_time)
        if not att or att.score == 0:
            return []
            
        weight_map = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
        weight = weight_map.get(att.attention_level.value, 1)
        
        summary = f"Requires {att.attention_level.value} attention (score: {att.score}). Reasons: {', '.join(att.reasons)}."
        
        return [EvidenceItem(
            evidence_id=_make_evidence_id(EvidenceType.ATTENTION, entity_id, None, att.attention_id),
            evidence_type=EvidenceType.ATTENTION,
            entity_id=entity_id,
            timestamp=att.evaluated_at,
            summary=summary,
            severity_weight=weight,
            type_priority=_get_priority(intent, EvidenceType.ATTENTION),
            metadata={"level": att.attention_level.value, "score": att.score},
            source_reference="Attention Engine",
        )]

    def _fetch_insight_evidence(
        self, intent: QueryIntent, entity_id: str, current_time: datetime, include_source_text: bool
    ) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        insights = self._insight_svc.get_entity_insights(entity_id, current_time)
        
        for ins in insights:
            weight_map = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "INFO": 1}
            weight = weight_map.get(ins.severity.value, 1)
            
            summary = f"Insight ({ins.insight_type.value}): {ins.title}. {ins.description}"
            
            source_text = None
            if include_source_text and ins.evidence and ins.evidence[0].source_text:
                source_text = ins.evidence[0].source_text
                
            ref = "Insight Engine"
            if ins.related_meeting_id:
                meeting = self._meeting_repo.get_by_id(ins.related_meeting_id)
                if meeting:
                    ref = f"Meeting: {meeting.title}"

            items.append(EvidenceItem(
                evidence_id=_make_evidence_id(EvidenceType.INSIGHT, entity_id, ins.related_meeting_id, ins.insight_id),
                evidence_type=EvidenceType.INSIGHT,
                entity_id=entity_id,
                meeting_id=ins.related_meeting_id,
                source_text=source_text,
                timestamp=ins.observed_at,
                summary=summary,
                severity_weight=weight,
                type_priority=_get_priority(intent, EvidenceType.INSIGHT),
                metadata={"severity": ins.severity.value},
                source_reference=ref,
            ))
            
        return items

    def _fetch_impact_evidence(
        self, intent: QueryIntent, entity_id: str, current_time: datetime, multi_hop: bool = False
    ) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        if multi_hop:
            impacts = self._impact_svc.get_entity_impacts_multi_hop(entity_id, current_time)
        else:
            impacts = self._impact_svc.get_entity_impacts(entity_id, current_time)
            
        for imp in impacts:
            weight_map = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
            weight = weight_map.get(imp.impact_level.value, 1)
            
            src = self._entity_repo.get_by_id(imp.source_entity_id)
            tgt = self._entity_repo.get_by_id(imp.impacted_entity_id)
            src_name = src.canonical_name if src else imp.source_entity_id
            tgt_name = tgt.canonical_name if tgt else imp.impacted_entity_id
            
            summary = f"Impact: '{src_name}' impacts '{tgt_name}' (Level: {imp.impact_level.value}). "
            summary += f"Reason: {imp.reason}. Depth: {imp.dependency_depth} hop(s)."
            
            items.append(EvidenceItem(
                evidence_id=_make_evidence_id(EvidenceType.IMPACT, entity_id, None, imp.impact_id),
                evidence_type=EvidenceType.IMPACT,
                entity_id=entity_id,
                timestamp=imp.generated_from_at,
                summary=summary,
                severity_weight=weight,
                type_priority=_get_priority(intent, EvidenceType.IMPACT),
                metadata={"impact_level": imp.impact_level.value, "depth": imp.dependency_depth},
                source_reference="Impact Analysis Engine",
            ))
            
        return items

    def _fetch_dependency_evidence(self, intent: QueryIntent, entity_id: str) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        
        # Get transitive dependants (things this entity blocks)
        trans_deps = self._dependency_graph_svc.get_transitive_dependencies(entity_id)
        for path in trans_deps:
            start_e = self._entity_repo.get_by_id(path.start_entity_id)
            end_e = self._entity_repo.get_by_id(path.end_entity_id)
            start_name = start_e.canonical_name if start_e else path.start_entity_id
            end_name = end_e.canonical_name if end_e else path.end_entity_id
            
            summary = f"Dependency Path: '{start_name}' depends on '{end_name}' across {path.depth} hop(s)."
            
            items.append(EvidenceItem(
                evidence_id=_make_evidence_id(EvidenceType.DEPENDENCY_PATH, entity_id, None, path.path_id),
                evidence_type=EvidenceType.DEPENDENCY_PATH,
                entity_id=entity_id,
                summary=summary,
                severity_weight=3 if path.depth == 1 else 2,
                type_priority=_get_priority(intent, EvidenceType.DEPENDENCY_PATH),
                metadata={"depth": path.depth},
                source_reference="Dependency Graph",
            ))
            
        return items
        
    def _fetch_relationship_evidence(self, intent: QueryIntent, entity_id: str, include_source_text: bool) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        rels = self._relationship_svc.get_dependency_relationships(entity_id)
        for rel in rels:
            src = self._entity_repo.get_by_id(rel.source_entity_id)
            tgt = self._entity_repo.get_by_id(rel.target_entity_id)
            src_name = src.canonical_name if src else rel.source_entity_id
            tgt_name = tgt.canonical_name if tgt else rel.target_entity_id
            
            summary = f"Explicit Relationship: '{src_name}' {rel.relationship_type.value} '{tgt_name}'."
            summary += f" Observed in {rel.strength} meeting(s)."
            
            source_text = None
            if include_source_text and rel.source_text:
                source_text = rel.source_text
                
            items.append(EvidenceItem(
                evidence_id=_make_evidence_id(EvidenceType.RELATIONSHIP, entity_id, None, rel.relationship_id),
                evidence_type=EvidenceType.RELATIONSHIP,
                entity_id=entity_id,
                source_text=source_text,
                summary=summary,
                severity_weight=3,
                type_priority=_get_priority(intent, EvidenceType.RELATIONSHIP),
                metadata={"type": rel.relationship_type.value},
                source_reference="Relationship Engine",
            ))
        return items

    def _fetch_action_evidence(
        self, intent: QueryIntent, entity_id: str, current_time: datetime
    ) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        actions = self._action_svc.get_entity_actions(entity_id, current_time)
        
        for act in actions:
            weight_map = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
            weight = weight_map.get(act.priority.value, 1)
            
            summary = f"Recommended Action ({act.action_type.value}): {act.recommended_action}. Reason: {act.reason}."
            
            ref = "Decision Support Engine"
            if act.related_meeting_id:
                meeting = self._meeting_repo.get_by_id(act.related_meeting_id)
                if meeting:
                    ref = f"Meeting: {meeting.title}"
                    
            items.append(EvidenceItem(
                evidence_id=_make_evidence_id(EvidenceType.ACTION, entity_id, act.related_meeting_id, act.action_id),
                evidence_type=EvidenceType.ACTION,
                entity_id=entity_id,
                meeting_id=act.related_meeting_id,
                timestamp=act.created_from_observation_at,
                summary=summary,
                severity_weight=weight,
                type_priority=_get_priority(intent, EvidenceType.ACTION),
                metadata={"priority": act.priority.value},
                source_reference=ref,
            ))
            
        return items

    def _fetch_org_change_evidence(
        self, intent: QueryIntent, current_time: datetime, entity_id: Optional[str], limit: int, include_source_text: bool
    ) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        changes = self._org_change_svc.get_changes(
            current_time=current_time,
            entity_id=entity_id,
            limit=limit,
        )
        
        for chg in changes:
            weight_map = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "INFO": 1}
            weight = weight_map.get(chg.severity.value, 1)
            
            e = self._entity_repo.get_by_id(chg.entity_id)
            name = e.canonical_name if e else chg.entity_id
            
            summary = f"Organisation Change ({chg.change_type.value}) for '{name}'. "
            if chg.previous_state and chg.current_state:
                summary += f"State: {chg.previous_state.value} -> {chg.current_state.value}."
            
            source_text = None
            if include_source_text and chg.source_text:
                source_text = chg.source_text
                
            ref = "Change Intelligence Engine"
            if chg.meeting_id:
                meeting = self._meeting_repo.get_by_id(chg.meeting_id)
                if meeting:
                    ref = f"Meeting: {meeting.title}"

            items.append(EvidenceItem(
                evidence_id=_make_evidence_id(EvidenceType.ORGANISATION_CHANGE, chg.entity_id, chg.meeting_id, chg.change_id),
                evidence_type=EvidenceType.ORGANISATION_CHANGE,
                entity_id=chg.entity_id,
                meeting_id=chg.meeting_id,
                mention_id=chg.mention_id,
                source_text=source_text,
                timestamp=chg.detected_at,
                summary=summary,
                severity_weight=weight,
                type_priority=_get_priority(intent, EvidenceType.ORGANISATION_CHANGE),
                metadata={"severity": chg.severity.value},
                source_reference=ref,
            ))
            
        return items

    def _fetch_portfolio_evidence(self, intent: QueryIntent, current_time: datetime, risk_only: bool = False) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        portfolio = self._portfolio_svc.get_portfolio(current_time)
        
        summary = f"Portfolio overview: {portfolio.total_entities} entities tracked. "
        summary += f"Critical: {portfolio.critical_entities}, High Risk: {portfolio.high_risk_entities}, "
        summary += f"Blocked: {portfolio.blocked_entities}, Actions needed: {portfolio.entities_with_active_actions}."
        
        items.append(EvidenceItem(
            evidence_id=_make_evidence_id(EvidenceType.MEMORY_FACT, None, None, "portfolio_summary"),
            evidence_type=EvidenceType.MEMORY_FACT,
            summary=summary,
            timestamp=portfolio.evaluated_at,
            severity_weight=4 if portfolio.critical_entities > 0 else 3,
            type_priority=_get_priority(intent, EvidenceType.MEMORY_FACT),
            source_reference="Portfolio Intelligence Engine",
        ))
        
        for pe in portfolio.entities:
            if risk_only and pe.risk_level not in (PortfolioRiskLevel.CRITICAL, PortfolioRiskLevel.HIGH):
                continue
                
            weight_map = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
            weight = weight_map.get(pe.risk_level.value, 1)
            
            pe_summary = f"Entity '{pe.canonical_name}' is at {pe.risk_level.value} risk. "
            if pe.attention_level != "UNKNOWN":
                pe_summary += f"Attention level: {pe.attention_level}. "
            if pe.active_insight_count > 0:
                pe_summary += f"Insights: {pe.active_insight_count}. "
            if pe.action_count > 0:
                pe_summary += f"Actions required: {pe.action_count}."
                
            items.append(EvidenceItem(
                evidence_id=_make_evidence_id(EvidenceType.MEMORY_FACT, pe.entity_id, None, "portfolio_entity"),
                evidence_type=EvidenceType.MEMORY_FACT,
                entity_id=pe.entity_id,
                summary=pe_summary,
                timestamp=portfolio.evaluated_at,
                severity_weight=weight,
                type_priority=_get_priority(intent, EvidenceType.MEMORY_FACT),
                source_reference="Portfolio Intelligence Engine",
            ))
            
        return items

    def _fetch_top_attention_evidence(self, intent: QueryIntent, current_time: datetime, limit: int) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        all_att = self._attention_svc.get_attention(current_time)
        
        # Sort by score desc
        all_att.sort(key=lambda x: x.score, reverse=True)
        top = all_att[:limit]
        
        for att in top:
            weight_map = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
            weight = weight_map.get(att.attention_level.value, 1)
            
            e = self._entity_repo.get_by_id(att.entity_id)
            name = e.canonical_name if e else att.entity_id
            
            summary = f"Entity '{name}' requires {att.attention_level.value} attention (score: {att.score}). Reasons: {', '.join(att.reasons)}."
            
            items.append(EvidenceItem(
                evidence_id=_make_evidence_id(EvidenceType.ATTENTION, att.entity_id, None, att.attention_id),
                evidence_type=EvidenceType.ATTENTION,
                entity_id=att.entity_id,
                timestamp=att.evaluated_at,
                summary=summary,
                severity_weight=weight,
                type_priority=_get_priority(intent, EvidenceType.ATTENTION),
                source_reference="Attention Engine",
            ))
            
        return items
