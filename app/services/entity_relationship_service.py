"""Entity relationship inference service (Stage 12).

Infers relationships between canonical entities purely from deterministic
evidence (meeting co-occurrence) available in the ingestion system.

This service is entirely read-only. It does not modify entities, mentions,
meetings, or any other data.
"""

import uuid
from typing import Dict, List, Set, Tuple

from app.models.entity import ResolutionStatus
from app.models.relationships import (
    EntityRelationship,
    EntityRelationshipGraph,
    RelationshipEvidenceType,
    RelationshipType,
)
from app.repositories.entity_repository import AbstractEntityRepository
from app.repositories.mention_repository import AbstractMentionRepository
from app.services.entity_service import EntityNotFoundError


class EntityRelationshipService:
    """Infers entity relationships deterministically."""

    def __init__(
        self,
        entity_repo: AbstractEntityRepository,
        mention_repo: AbstractMentionRepository,
    ) -> None:
        self._entity_repo = entity_repo
        self._mention_repo = mention_repo

    def get_relationship_graph(self, entity_id: str) -> EntityRelationshipGraph:
        """Return the relationship graph for a specific entity.

        Args:
            entity_id: The ID of the canonical entity.

        Raises:
            EntityNotFoundError: If the entity does not exist.
        """
        # 1. Validate entity existence
        if self._entity_repo.get_by_id(entity_id) is None:
            raise EntityNotFoundError(f"Entity '{entity_id}' not found.")

        # 2. Get all mentions for this entity to find which meetings it appeared in
        mentions = self._mention_repo.list_by_entity_id(entity_id)
        meeting_ids = {m.meeting_id for m in mentions}

        # 3. Track co-occurrences with other entities
        # target_entity_id -> set of meeting_ids where they co-occurred
        co_occurrences: Dict[str, Set[str]] = {}

        for meeting_id in meeting_ids:
            # Get all mentions in this meeting
            meeting_mentions = self._mention_repo.list_by_meeting_id(meeting_id)
            for m in meeting_mentions:
                if m.resolution_status == ResolutionStatus.RESOLVED and m.entity_id:
                    # Ignore self-relationships
                    if m.entity_id != entity_id:
                        if m.entity_id not in co_occurrences:
                            co_occurrences[m.entity_id] = set()
                        co_occurrences[m.entity_id].add(meeting_id)

        # 4. Generate deterministic relationships
        relationships: List[EntityRelationship] = []
        for target_id, shared_meetings in co_occurrences.items():
            # Calculate deterministic sort key and strength
            strength = len(shared_meetings)
            shared_meetings_list = sorted(list(shared_meetings))
            
            # For symmetric deduplication of the underlying ID, we need a consistent
            # ordering of the two entity IDs, regardless of which one we query.
            canonical_a = min(entity_id, target_id)
            canonical_b = max(entity_id, target_id)
            
            # Generate deterministic UUID for this specific edge type
            rel_type = RelationshipType.CO_OCCURS_WITH.value
            ns_name = f"{canonical_a}:{canonical_b}:{rel_type}"
            relationship_id = str(uuid.uuid5(uuid.NAMESPACE_OID, ns_name))
            
            evidence_text = f"Entities co-occurred in {strength} meeting(s)."

            sort_key = f"{strength:06d}_{rel_type}_{target_id}_{relationship_id}"

            rel = EntityRelationship(
                relationship_id=relationship_id,
                source_entity_id=canonical_a,
                target_entity_id=canonical_b,
                relationship_type=RelationshipType.CO_OCCURS_WITH,
                evidence_type=RelationshipEvidenceType.CO_OCCURRENCE,
                evidence=evidence_text,
                related_meeting_ids=shared_meetings_list,
                strength=strength,
                deterministic_sort_key=sort_key
            )
            relationships.append(rel)

        # 5. Sort relationships deterministically (strength DESC, then target_id ASC, etc.)
        relationships.sort(key=lambda r: (-r.strength, r.relationship_type.value, r.target_entity_id, r.relationship_id))
        
        # 6. Re-assign sort keys to reflect final values (strength not inverted)
        for rel in relationships:
             rel.deterministic_sort_key = f"{rel.strength:06d}_{rel.relationship_type.value}_{rel.target_entity_id}_{rel.relationship_id}"

        return EntityRelationshipGraph(
            entity_id=entity_id,
            relationships=relationships,
            related_entity_ids=sorted(list(co_occurrences.keys())),
            relationship_count=len(relationships)
        )
