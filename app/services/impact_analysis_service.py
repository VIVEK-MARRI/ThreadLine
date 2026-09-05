"""Service layer for the Cross-Entity Risk Propagation & Impact Analysis Engine (Stage 13).

This service is purely read-only and deterministic. It synthesizes intelligence
by combining Relationships, Temporal States, Insights, and Attention.
"""

import hashlib
from datetime import datetime
from typing import List

from app.models.attention import AttentionLevel
from app.models.impact import EntityImpact, ImpactLevel, RiskSignalType
from app.models.insights import InsightType
from app.models.temporal import TemporalState
from app.repositories.entity_repository import AbstractEntityRepository
from app.services.attention_service import AttentionService
from app.services.entity_relationship_service import EntityRelationshipService
from app.services.entity_service import EntityNotFoundError
from app.services.insight_service import InsightService
from app.services.temporal_state_service import TemporalStateService


class ImpactAnalysisService:
    """Infers risk impact associations across entities deterministically."""

    def __init__(
        self,
        entity_repo: AbstractEntityRepository,
        relationship_service: EntityRelationshipService,
        temporal_service: TemporalStateService,
        insight_service: InsightService,
        attention_service: AttentionService,
    ) -> None:
        self._entity_repo = entity_repo
        self._relationship_service = relationship_service
        self._temporal_service = temporal_service
        self._insight_service = insight_service
        self._attention_service = attention_service

    def get_entity_impacts(self, entity_id: str, current_time: datetime) -> List[EntityImpact]:
        """Return the impact associations directed at a specific entity.

        This answers: "What risks from other associated entities are impacting this entity?"
        
        Args:
            entity_id: The ID of the canonical entity being impacted.
            current_time: The timestamp for evaluating current attention/staleness.

        Raises:
            EntityNotFoundError: If the entity does not exist.
        """
        if self._entity_repo.get_by_id(entity_id) is None:
            raise EntityNotFoundError(f"Entity '{entity_id}' not found.")

        impacts: List[EntityImpact] = []

        # 1. Get all entities that co-occur with this one
        graph = self._relationship_service.get_relationship_graph(entity_id)
        
        # Build a mapping from related_entity_id to its relationship edge
        related_map = {}
        for rel in graph.relationships:
            # Determine the other entity in the edge
            other_id = rel.source_entity_id if rel.target_entity_id == entity_id else rel.target_entity_id
            if other_id != entity_id:
                related_map[other_id] = rel
        
        # 2. For each related entity (the source of potential risk), evaluate its risk signals
        for source_id, rel in related_map.items():
            risk_signals = set()
            
            # 2a. Temporal State
            timeline = self._temporal_service.get_entity_timeline(source_id)
            if timeline.current_state == TemporalState.BLOCKED:
                risk_signals.add(RiskSignalType.BLOCKED_ENTITY)
            
            # 2b. Attention
            attention = self._attention_service.get_entity_attention(source_id, current_time)
            if attention:
                if attention.attention_level == AttentionLevel.CRITICAL:
                    risk_signals.add(RiskSignalType.CRITICAL_ATTENTION)
                elif attention.attention_level == AttentionLevel.HIGH:
                    risk_signals.add(RiskSignalType.HIGH_ATTENTION)
                    
            # 2c. Insights
            insights = self._insight_service.get_entity_insights(source_id, current_time)
            for insight in insights:
                if insight.insight_type == InsightType.REOPEN_ATTEMPT:
                    risk_signals.add(RiskSignalType.REOPEN_ATTEMPT)
                elif insight.insight_type == InsightType.STALE_ENTITY:
                    risk_signals.add(RiskSignalType.STALE_ENTITY)
                elif insight.insight_type == InsightType.REPEATED_OBSERVATION:
                    risk_signals.add(RiskSignalType.REPEATED_OBSERVATION)
            
            if not risk_signals:
                continue
                
            sorted_signals = sorted(list(risk_signals), key=lambda s: s.value)
            
            # 3. Determine impact level and reason
            is_strong = rel.strength >= 2
            
            if RiskSignalType.CRITICAL_ATTENTION in sorted_signals and is_strong:
                impact_level = ImpactLevel.CRITICAL
                reason = "The impacted entity frequently co-occurs with an entity that requires CRITICAL attention."
            elif RiskSignalType.BLOCKED_ENTITY in sorted_signals:
                impact_level = ImpactLevel.HIGH
                reason = "The impacted entity frequently co-occurs with an entity currently in a BLOCKED state."
            elif RiskSignalType.CRITICAL_ATTENTION in sorted_signals and not is_strong:
                impact_level = ImpactLevel.HIGH
                reason = "The impacted entity is associated with an entity that requires CRITICAL attention."
            elif RiskSignalType.HIGH_ATTENTION in sorted_signals and is_strong:
                impact_level = ImpactLevel.HIGH
                reason = "The impacted entity frequently co-occurs with an entity that requires HIGH attention."
            elif RiskSignalType.REOPEN_ATTEMPT in sorted_signals:
                impact_level = ImpactLevel.MEDIUM
                reason = "The impacted entity is associated with an entity that had a recent reopen attempt."
            elif RiskSignalType.HIGH_ATTENTION in sorted_signals and not is_strong:
                impact_level = ImpactLevel.MEDIUM
                reason = "The impacted entity is associated with an entity that requires HIGH attention."
            else:
                impact_level = ImpactLevel.LOW
                reason = "The impacted entity is associated with an entity that has active risk signals."
                
            # 4. Generate deterministic ID and sort key
            # Hash source_id + impacted_id to ensure deduplication/stability
            hash_input = f"{source_id}:{entity_id}:{impact_level.value}:{rel.strength}".encode("utf-8")
            impact_id = hashlib.sha256(hash_input).hexdigest()[:16]
            
            # Level value for sorting (CRITICAL=4, HIGH=3, MEDIUM=2, LOW=1)
            level_map = {
                ImpactLevel.CRITICAL: 4,
                ImpactLevel.HIGH: 3,
                ImpactLevel.MEDIUM: 2,
                ImpactLevel.LOW: 1
            }
            sort_val = level_map[impact_level]
            
            sort_key = f"{sort_val}_{rel.strength:06d}_{source_id}_{entity_id}_{impact_id}"
            
            impact = EntityImpact(
                impact_id=impact_id,
                source_entity_id=source_id,
                impacted_entity_id=entity_id,
                impact_level=impact_level,
                risk_signals=sorted_signals,
                relationship_strength=rel.strength,
                related_meeting_ids=rel.related_meeting_ids,
                reason=reason,
                generated_from_at=current_time,
                deterministic_sort_key=sort_key
            )
            impacts.append(impact)

        # 5. Sort impacts deterministically
        # Rule R10: impact_level DESC, relationship_strength DESC, source_entity_id ASC, impacted_entity_id ASC, impact_id ASC
        impacts.sort(key=lambda i: (
            -level_map[i.impact_level],
            -i.relationship_strength,
            i.source_entity_id,
            i.impacted_entity_id,
            i.impact_id
        ))

        return impacts
