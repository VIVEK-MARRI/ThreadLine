"""Entity relationship inference service (Stage 12+15).

Infers relationships between canonical entities from deterministic evidence:
- CO_OCCURS_WITH: derived from meeting co-occurrence in the ingestion system.
- DEPENDS_ON / BLOCKS: derived from explicit explicit dependency records (Stage 15).

This service is entirely read-only. It does not modify entities, mentions,
meetings, or any other data.
"""

import hashlib
import uuid
from typing import Dict, List, Optional, Set, Tuple

from app.models.dependency import ExplicitDependency
from app.models.entity import ResolutionStatus
from app.models.relationships import (
    EntityRelationship,
    EntityRelationshipGraph,
    RelationshipEvidenceType,
    RelationshipType,
)
from app.repositories.dependency_repository import AbstractDependencyRepository
from app.repositories.entity_repository import AbstractEntityRepository
from app.repositories.mention_repository import AbstractMentionRepository
from app.services.entity_service import EntityNotFoundError


class EntityRelationshipService:
    """Infers entity relationships deterministically."""

    def __init__(
        self,
        entity_repo: AbstractEntityRepository,
        mention_repo: AbstractMentionRepository,
        dependency_repo: Optional[AbstractDependencyRepository] = None,
        current_revision_lookup=None,
    ) -> None:
        self._entity_repo = entity_repo
        self._mention_repo = mention_repo
        self._dependency_repo = dependency_repo
        # Optional callable mapping meeting_id -> current source revision.
        # When provided, only current-revision dependencies/mentions shape the
        # inferred relationships; stale/future evidence never masquerades.
        self._current_revision_lookup = current_revision_lookup

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

        relationships: List[EntityRelationship] = []
        related_entities: Set[str] = set()

        # 2. Get CO_OCCURS_WITH relationships
        co_occurrence_rels = self._get_co_occurrences(entity_id)
        relationships.extend(co_occurrence_rels)
        for r in co_occurrence_rels:
            related_entities.add(r.source_entity_id)
            related_entities.add(r.target_entity_id)

        # 3. Get explicit dependency relationships (if available)
        if self._dependency_repo is not None:
            explicit_rels = self._get_explicit_dependencies(entity_id)
            relationships.extend(explicit_rels)
            for r in explicit_rels:
                related_entities.add(r.source_entity_id)
                related_entities.add(r.target_entity_id)

        # Remove self from related_entities
        related_entities.discard(entity_id)

        # 4. Sort relationships deterministically
        # (strength DESC, then relationship_type ASC, then target_entity_id ASC, etc.)
        relationships.sort(
            key=lambda r: (
                -r.strength,
                r.relationship_type.value,
                r.target_entity_id if r.source_entity_id == entity_id else r.source_entity_id,
                r.relationship_id,
            )
        )
        
        # 5. Re-assign sort keys to reflect final values (strength not inverted)
        for rel in relationships:
            target_id_for_sort = rel.target_entity_id if rel.source_entity_id == entity_id else rel.source_entity_id
            rel.deterministic_sort_key = f"{rel.strength:06d}_{rel.relationship_type.value}_{target_id_for_sort}_{rel.relationship_id}"

        return EntityRelationshipGraph(
            entity_id=entity_id,
            relationships=relationships,
            related_entity_ids=sorted(list(related_entities)),
            relationship_count=len(relationships)
        )

    def get_dependency_relationships(self, entity_id: str) -> List[EntityRelationship]:
        """Return only the explicit dependency relationships for a specific entity.

        Raises:
            EntityNotFoundError: If the entity does not exist.
        """
        if self._entity_repo.get_by_id(entity_id) is None:
            raise EntityNotFoundError(f"Entity '{entity_id}' not found.")

        if self._dependency_repo is None:
            return []

        relationships = self._get_explicit_dependencies(entity_id)
        
        # Sort relationships deterministically
        relationships.sort(
            key=lambda r: (
                -r.strength,
                r.relationship_type.value,
                r.target_entity_id if r.source_entity_id == entity_id else r.source_entity_id,
                r.relationship_id,
            )
        )
        
        # Re-assign sort keys
        for rel in relationships:
            target_id_for_sort = rel.target_entity_id if rel.source_entity_id == entity_id else rel.source_entity_id
            rel.deterministic_sort_key = f"{rel.strength:06d}_{rel.relationship_type.value}_{target_id_for_sort}_{rel.relationship_id}"

        return relationships

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_co_occurrences(self, entity_id: str) -> List[EntityRelationship]:
        mentions = self._mention_repo.list_current_by_entity_id(
            entity_id, self._current_revision_lookup
        )
        meeting_ids = {m.meeting_id for m in mentions}

        # target_entity_id -> set of meeting_ids where they co-occurred
        co_occurrences: Dict[str, Set[str]] = {}

        for meeting_id in meeting_ids:
            meeting_mentions = self._mention_repo.list_current_by_meeting_id(
                meeting_id, self._current_revision_lookup
            )
            for m in meeting_mentions:
                if m.resolution_status == ResolutionStatus.RESOLVED and m.entity_id:
                    # Ignore self-relationships
                    if m.entity_id != entity_id:
                        if m.entity_id not in co_occurrences:
                            co_occurrences[m.entity_id] = set()
                        co_occurrences[m.entity_id].add(meeting_id)

        relationships: List[EntityRelationship] = []
        for target_id, shared_meetings in co_occurrences.items():
            strength = len(shared_meetings)
            shared_meetings_list = sorted(list(shared_meetings))
            
            canonical_a = min(entity_id, target_id)
            canonical_b = max(entity_id, target_id)
            
            rel_type = RelationshipType.CO_OCCURS_WITH.value
            ns_name = f"{canonical_a}:{canonical_b}:{rel_type}"
            relationship_id = str(uuid.uuid5(uuid.NAMESPACE_OID, ns_name))
            
            evidence_text = f"Entities co-occurred in {strength} meeting(s)."

            # Placeholder sort key, will be updated in caller
            sort_key = ""

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
        return relationships

    def _get_explicit_dependencies(self, entity_id: str) -> List[EntityRelationship]:
        if self._dependency_repo is None:
            return []
            
        deps = self._dependency_repo.list_current_by_entity_id(
            entity_id, self._current_revision_lookup
        )
        
        # Deduplicate logical relationships:
        # Keyed by (source_entity_id, target_entity_id, relationship_type)
        grouped_deps: Dict[Tuple[str, str, RelationshipType], List[ExplicitDependency]] = {}
        for dep in deps:
            key = (dep.source_entity_id, dep.target_entity_id, dep.relationship_type)
            if key not in grouped_deps:
                grouped_deps[key] = []
            grouped_deps[key].append(dep)
            
        relationships: List[EntityRelationship] = []
        for (src_id, tgt_id, rel_type), group in grouped_deps.items():
            # Gather all distinct meetings for this logical relationship
            meeting_ids = set()
            for dep in group:
                meeting_ids.add(dep.meeting_id)
            
            shared_meetings_list = sorted(list(meeting_ids))
            strength = len(shared_meetings_list)
            
            # Use the first dependency record (by sorted meeting ID) to supply the primary evidence text
            group.sort(key=lambda d: d.meeting_id)
            primary_evidence = group[0]
            
            # Generate deterministic relationship ID for this logical edge
            # This is different from dependency_id (which is tied to a specific meeting)
            raw_id = f"{src_id}:{tgt_id}:{rel_type.value}"
            relationship_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]
            
            evidence_text = f"Explicitly stated in {strength} meeting(s)."

            rel = EntityRelationship(
                relationship_id=relationship_id,
                source_entity_id=src_id,
                target_entity_id=tgt_id,
                relationship_type=rel_type,
                evidence_type=RelationshipEvidenceType.EXPLICIT_STATEMENT,
                evidence=evidence_text,
                source_text=primary_evidence.source_text,
                mention_id=primary_evidence.mention_id,
                related_meeting_ids=shared_meetings_list,
                strength=strength,
                deterministic_sort_key="", # placeholder
            )
            relationships.append(rel)
            
        return relationships
