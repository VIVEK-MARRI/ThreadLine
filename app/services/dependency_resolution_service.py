"""Dependency Resolution Service (Stage 15).

Resolves explicit dependency statements extracted from entity mention source_texts
into canonical ExplicitDependency records.

Pipeline
--------
For each resolved entity mention (entity_id is known):
  1. Run KeywordExtractor on mention.source_text.
  2. For each ExtractedRelationStatement:
     a. SOURCE validation: the extracted source_ref must match the resolved
        mention's entity (canonical_name or an alias).  If the source_ref
        does NOT match the mention's entity, the statement is discarded —
        we must never fabricate a dependency for the wrong entity.
     b. TARGET entity name = statement.target_ref → look up via entity_repo.
     c. If target resolves → create ExplicitDependency and save to repo.
     d. If target does not resolve → discard (no fabrication).
  3. Apply resolution rules:
     - source and target must be distinct entities.
     - relationship_type must be DEPENDS_ON or BLOCKS.
     - Both participants must be canonical entities.
     - Evidence must exist (source_text is non-empty).

Source reference validation rationale
--------------------------------------
Consider a mention whose source_text is:
    "Auth Service blocks Frontend."

If this mention was resolved to entity "Auth Service", then the extractor
produces source_ref="Auth Service", which MATCHES the mention's entity →
relationship is created.

If the same mention was incorrectly resolved to "Frontend" (a resolution
error in the pipeline), we must NOT silently create "Frontend BLOCKS Frontend"
or "Frontend BLOCKS some_other_entity".  The validation step catches this:
source_ref="Auth Service" does NOT match "Frontend" → relationship discarded.

Target entity type search order
---------------------------------
The _resolve_target helper searches entity types in a fixed, deterministic
order: ISSUE first, then PERSON.  This order is conservative and explicit.
If the architecture later introduces additional entity types, this list must
be extended here.

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

IMPORTANT: This service never invents entity IDs or fabricates relationships.
An unresolved target or a source mismatch simply produces no relationship record.
"""

import hashlib
import logging

from app.dependency_extraction.keyword_extractor import extract_relationship_statements
from app.models.dependency import ExplicitDependency
from app.models.entity import EntityType
from app.models.relationships import RelationshipEvidenceType, RelationshipType
from app.repositories.dependency_repository import AbstractDependencyRepository
from app.repositories.entity_repository import AbstractEntityRepository, _normalize
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


def _source_ref_matches_entity(
    source_ref: str,
    entity_canonical_name: str,
    entity_aliases: list[str],
) -> bool:
    """Return True if source_ref matches the entity's name or any alias.

    Comparison is case-insensitive and whitespace-normalised, using the same
    _normalize() function as the entity repository.

    This guards against fabricating a relationship where the mention was
    resolved to entity A but the extracted relationship statement refers to
    entity B as the source.
    """
    normalised_ref = _normalize(source_ref)
    if normalised_ref == _normalize(entity_canonical_name):
        return True
    for alias in entity_aliases:
        if normalised_ref == _normalize(alias):
            return True
    return False


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

        Runs the keyword extractor on the mention's source_text, validates the
        source participant against the resolved entity, attempts to resolve the
        target participant to a canonical entity, and stores any successfully
        resolved ExplicitDependency records.

        Parameters
        ----------
        mention_id:
            The ID of the entity mention to process.

        Returns
        -------
        list[ExplicitDependency]
            All ExplicitDependency records successfully resolved and stored.
            Empty list if the mention is unresolved, has no source_text,
            no explicit dependency language was found, or all statements
            failed source validation or target resolution.

        Notes
        -----
        Silently skips unresolved mentions (entity_id is None).
        Silently discards statements where source_ref does not match the mention's entity.
        Silently discards statements where the target does not resolve.
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

        # Fetch the source entity to validate the extracted source_ref.
        source_entity = self._entity_repo.get_by_id(source_entity_id)
        if source_entity is None:
            logger.debug(
                "DependencyResolutionService: source entity '%s' not found; skipping mention '%s'.",
                source_entity_id,
                mention_id,
            )
            return []

        statements = extract_relationship_statements(mention.source_text)
        if not statements:
            return []

        resolved: list[ExplicitDependency] = []

        for stmt in statements:
            # Source validation: verify that the extracted source_ref corresponds
            # to the mention's resolved entity.  This prevents creating a
            # dependency for the wrong entity when the source_text contains a
            # statement about a different entity.
            if not _source_ref_matches_entity(
                source_ref=stmt.source_ref,
                entity_canonical_name=source_entity.canonical_name,
                entity_aliases=source_entity.aliases,
            ):
                logger.debug(
                    "DependencyResolutionService: source_ref %r does not match "
                    "entity '%s' ('%s'); discarding %s statement from mention '%s'.",
                    stmt.source_ref,
                    source_entity_id,
                    source_entity.canonical_name,
                    stmt.relationship_type.value,
                    mention_id,
                )
                continue

            target_ref = stmt.target_ref

            # Try to resolve target across entity types (conservative: name match only)
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

        Tries entity types in a fixed, deterministic order:
          1. ISSUE
          2. PERSON

        Returns the first match, or None if no canonical entity matches the
        normalised name.

        This is conservative: only exact normalised name matches succeed.
        Fuzzy or semantic matching is intentionally not performed here.

        Entity type search order is ISSUE-first because:
        - Dependency language in meeting transcripts most commonly refers
          to ISSUE-type entities (projects, tasks, system components).
        - If the same name exists as both an ISSUE and a PERSON, the ISSUE
          interpretation is more likely in a dependency context.
        """
        for entity_type in [EntityType.ISSUE, EntityType.PERSON]:
            entity = self._entity_repo.find_by_canonical_name(
                name=target_ref,
                entity_type=entity_type,
            )
            if entity is not None:
                return entity
        return None
