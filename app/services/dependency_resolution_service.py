"""Dependency Resolution Service (Stage 15).

Resolves explicit dependency statements extracted from entity mention source_texts
into canonical ExplicitDependency records.

Pipeline
--------
For each resolved entity mention (entity_id is known):
  1. Run KeywordExtractor on mention.source_text.
  2. For each ExtractedRelationStatement:
     a. SOURCE entity_id = mention.entity_id (already resolved).
     b. TARGET entity name = statement.target_ref → look up via entity_repo.
     c. If target resolves → create ExplicitDependency and save to repo.
     d. If target does not resolve → discard (no fabrication).
  3. Apply resolution rules:
     - source and target must be distinct entities.
     - relationship_type must be DEPENDS_ON or BLOCKS.
     - Both participants must be canonical entities.
     - Evidence must exist (source_text is non-empty).

Design notes
------------
- This service is intentionally separate from ExtractionService.
  Extraction deals with LLM-based fact extraction; dependency resolution
  deals with deterministic, evidence-backed relationship resolution from
  already-stored mentions.
- This service is write-capable (saves to dependency_repo) but never
  modifies entities, mentions, or meetings.
- dependency_id is deterministic:
    sha256(f"{source_id}:{target_id}:{rel_type}:{meeting_id}")[:16]
  Re-processing the same mention produces the same record (idempotent).
- entity_type is not enforced during target lookup — the extractor does not
  know whether "database migration" is an ISSUE or another type.  The
  find_by_canonical_name call searches across entity types by normalised name
  only.  If the architecture later requires type-scoped lookup, this can be
  changed without breaking the rest of the system.

IMPORTANT: This service never invents entity IDs or fabricates relationships.
An unresolved target simply produces no relationship record.
"""

import hashlib
import logging
from typing import Optional

from app.dependency_extraction.keyword_extractor import extract_relationship_statements
from app.models.dependency import ExplicitDependency
from app.models.entity import EntityType
from app.models.relationships import RelationshipEvidenceType, RelationshipType
from app.repositories.dependency_repository import AbstractDependencyRepository
from app.repositories.entity_repository import AbstractEntityRepository
from app.repositories.mention_repository import AbstractMentionRepository

logger = logging.getLogger(__name__)


def _make_dependency_id(
    source_entity_id: str,
    target_entity_id: str,
    relationship_type: RelationshipType,
    meeting_id: str,
) -> str:
    """Generate a deterministic dependency_id.

    sha256(source_id:target_id:rel_type:meeting_id)[:16]
    """
    raw = f"{source_entity_id}:{target_entity_id}:{relationship_type.value}:{meeting_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class DependencyResolutionService:
    """Resolves explicit dependency statements into canonical ExplicitDependency records.

    This service is write-capable — it saves resolved dependencies to the
    dependency repository.  It is idempotent: re-processing the same mention
    produces the same record with the same dependency_id.
    """

    def __init__(
        self,
        entity_repo: AbstractEntityRepository,
        mention_repo: AbstractMentionRepository,
        dependency_repo: AbstractDependencyRepository,
    ) -> None:
        self._entity_repo = entity_repo
        self._mention_repo = mention_repo
        self._dependency_repo = dependency_repo

    def resolve_mention_dependencies(self, mention_id: str) -> list[ExplicitDependency]:
        """Resolve explicit dependencies from a single entity mention.

        Runs the keyword extractor on the mention's source_text, attempts to
        resolve the target participant to a canonical entity, and stores any
        successfully resolved ExplicitDependency records.

        Parameters
        ----------
        mention_id:
            The ID of the entity mention to process.

        Returns
        -------
        list[ExplicitDependency]
            All ExplicitDependency records successfully resolved and stored.
            Empty list if the mention is unresolved, has no source_text,
            or no explicit dependency language was found.

        Notes
        -----
        Silently skips unresolved mentions (entity_id is None).
        Silently discards any statement where the target does not resolve.
        """
        mention = self._mention_repo.get_by_id(mention_id)
        if mention is None:
            logger.debug(
                "DependencyResolutionService: mention '%s' not found; skipping.",
                mention_id,
            )
            return []

        # Only process resolved mentions — we need a known source entity_id.
        if mention.entity_id is None:
            logger.debug(
                "DependencyResolutionService: mention '%s' is unresolved; skipping.",
                mention_id,
            )
            return []

        source_entity_id = mention.entity_id

        statements = extract_relationship_statements(mention.source_text)
        if not statements:
            return []

        resolved: list[ExplicitDependency] = []

        for stmt in statements:
            # The source participant in the statement is a raw text reference.
            # However, the mention itself belongs to the source entity — the
            # source entity is ALREADY RESOLVED.  We use mention.entity_id directly.
            #
            # The target participant name comes from the pattern match and must
            # be resolved via entity_repo.

            target_ref = stmt.target_ref

            # Try to resolve target across all entity types (conservative: name match only)
            target_entity = self._resolve_target(target_ref)

            if target_entity is None:
                logger.debug(
                    "DependencyResolutionService: target '%s' did not resolve; "
                    "discarding %s statement from mention '%s'.",
                    target_ref,
                    stmt.relationship_type.value,
                    mention_id,
                )
                continue

            target_entity_id = target_entity.entity_id

            # Rule: source and target must be distinct
            if source_entity_id == target_entity_id:
                logger.debug(
                    "DependencyResolutionService: self-relationship detected "
                    "(source == target == '%s'); discarding.",
                    source_entity_id,
                )
                continue

            # Rule: relationship_type must be DEPENDS_ON or BLOCKS
            if stmt.relationship_type not in (RelationshipType.DEPENDS_ON, RelationshipType.BLOCKS):
                logger.debug(
                    "DependencyResolutionService: unsupported relationship type '%s'; "
                    "discarding.",
                    stmt.relationship_type.value,
                )
                continue

            dep_id = _make_dependency_id(
                source_entity_id,
                target_entity_id,
                stmt.relationship_type,
                mention.meeting_id,
            )

            dependency = ExplicitDependency(
                dependency_id=dep_id,
                source_entity_id=source_entity_id,
                target_entity_id=target_entity_id,
                relationship_type=stmt.relationship_type,
                evidence_type=RelationshipEvidenceType.EXPLICIT_STATEMENT,
                source_text=stmt.source_text,
                meeting_id=mention.meeting_id,
                mention_id=mention_id,
            )

            self._dependency_repo.save(dependency)
            resolved.append(dependency)

            logger.info(
                "DependencyResolutionService: resolved %s: '%s' → '%s' "
                "(meeting=%s, mention=%s, pattern=%s)",
                stmt.relationship_type.value,
                source_entity_id,
                target_entity_id,
                mention.meeting_id,
                mention_id,
                stmt.matched_pattern,
            )

        return resolved

    def resolve_meeting_dependencies(self, meeting_id: str) -> list[ExplicitDependency]:
        """Resolve explicit dependencies from all resolved mentions in a meeting.

        Convenience method that iterates over all resolved mentions in a meeting
        and calls resolve_mention_dependencies for each.

        Parameters
        ----------
        meeting_id:
            The ID of the meeting to process.

        Returns
        -------
        list[ExplicitDependency]
            All ExplicitDependency records resolved across the meeting.
        """
        mentions = self._mention_repo.list_by_meeting_id(meeting_id)
        all_resolved: list[ExplicitDependency] = []
        for mention in mentions:
            if mention.entity_id is not None:
                resolved = self.resolve_mention_dependencies(mention.mention_id)
                all_resolved.extend(resolved)
        return all_resolved

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_target(self, target_ref: str):
        """Attempt to resolve a raw target text reference to a canonical entity.

        Tries all entity types in a deterministic order.  Returns the first
        match, or None if no canonical entity matches the normalised name.

        This is conservative: only exact normalised name matches succeed.
        Fuzzy or semantic matching is intentionally not performed here.
        """
        for entity_type in [EntityType.ISSUE, EntityType.PERSON]:
            entity = self._entity_repo.find_by_canonical_name(
                name=target_ref,
                entity_type=entity_type,
            )
            if entity is not None:
                return entity
        return None
