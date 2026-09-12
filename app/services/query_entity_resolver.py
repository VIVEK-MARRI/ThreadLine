"""Query Entity Resolver — read-only entity name resolution (Stage 18).

Resolves an entity name mentioned in a natural-language question to a
canonical entity_id by searching the entity repository.

Design principles
-----------------
- Read-only: does NOT modify entities, aliases, or any repository.
- Deterministic: same repository state + same name always produces same result.
- Conservative: does NOT perform fuzzy, phonetic, semantic, or embedding-based
  matching.  Exact normalized match only (case-insensitive, whitespace-collapsed).
- Explicit outcomes: RESOLVED / UNRESOLVED / AMBIGUOUS / NOT_REQUIRED.
- AMBIGUOUS is NEVER silently resolved: if multiple entities match, the caller
  must report ambiguity rather than choose arbitrarily.
- Does NOT modify the canonical entity resolution pipeline (Stages 1–12).
  This is a separate read-only query-side resolver.

Entity name extraction
----------------------
The resolver does NOT extract entity names from free-form text.
The question is matched against all known canonical names and aliases.
If the question contains a name that matches exactly one entity: RESOLVED.
If it matches multiple entities: AMBIGUOUS.
If it matches zero entities: UNRESOLVED.

The QueryEntityResolver does NOT guess whether an entity is present in
a question — it relies on the NaturalLanguageQueryService to call it
only when the intent suggests an entity-level query.
"""

import logging
import re
from typing import Optional

from app.models.entity import EntityType
from app.models.natural_language import (
    EntityResolutionResult,
    EntityResolutionStatus,
)
from app.repositories.entity_repository import AbstractEntityRepository

logger = logging.getLogger(__name__)


def _normalize_name(text: str) -> str:
    """Normalize for exact matching: lowercase, collapse whitespace, strip punctuation."""
    text = text.lower().strip()
    # Remove trailing punctuation that might appear at end of question
    text = re.sub(r'[?.!,;:]+$', '', text).strip()
    # Collapse internal whitespace
    return " ".join(text.split())


def _extract_candidate_names(question: str) -> list[str]:
    """Extract potential entity name substrings from the question.

    Strategy:
    1. Try the full question (stripped of question words).
    2. Try noun phrases by stripping leading question words.
    3. Try quoted strings if any.

    This is conservative. We only try substrings that are plausibly names.
    We do NOT use NLP, POS tagging, or NER — this is purely heuristic.
    """
    candidates: list[str] = []

    # Extract quoted phrases first (highest confidence)
    quoted = re.findall(r'"([^"]+)"', question)
    candidates.extend(quoted)

    # Strip leading question words and interrogatives
    _STRIP_PREFIXES = [
        r'^what is (?:the status of |the state of |blocking |wrong with )?',
        r'^what are (?:the risks (?:for|with|of) )?',
        r'^what happened to ',
        r'^what has happened (?:to |with )?',
        r'^what does .+? depend (?:on)?',
        r'^what blocks ',
        r'^what is blocking ',
        r"^what's blocking ",
        r'^how is ',
        r'^status of ',
        r'^status for ',
        r'^risks for ',
        r'^risks with ',
        r'^history of ',
        r'^timeline of ',
        r'^what should we do (?:about |with )?',
        r'^next steps for ',
        r'^actions for ',
        r'^what changed with ',
        r'^changes for ',
        r'^what impacts ',
        r'^what affects ',
        r'^impact on ',
        r'^affected by ',
        r'^follow up on ',
    ]

    normalized_q = question.lower().strip()
    for prefix_re in _STRIP_PREFIXES:
        remainder = re.sub(prefix_re, '', normalized_q, count=1)
        if remainder and remainder != normalized_q:
            # Strip trailing question marks / punctuation
            remainder = re.sub(r'[?.!,;:]+$', '', remainder).strip()
            if remainder:
                candidates.append(remainder)

    # Also try stripping "the " article from candidates
    expanded: list[str] = []
    for c in candidates:
        expanded.append(c)
        if c.startswith("the "):
            expanded.append(c[4:])
    candidates = expanded

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen and c:
            seen.add(c)
            unique.append(c)

    return unique


class QueryEntityResolver:
    """Read-only entity name resolver for natural-language queries.

    Resolves entity name references from questions by searching all
    canonical entities and aliases in the repository.
    """

    def __init__(self, entity_repo: AbstractEntityRepository) -> None:
        self._entity_repo = entity_repo

    def resolve(
        self,
        question: str,
        entity_type: Optional[EntityType] = None,
    ) -> EntityResolutionResult:
        """Attempt to resolve an entity name from the question.

        Parameters
        ----------
        question:
            The natural-language question text.
        entity_type:
            Optional filter; if provided, only entities of this type are searched.
            If None, all entity types are searched.

        Returns
        -------
        EntityResolutionResult
            RESOLVED, UNRESOLVED, AMBIGUOUS, or NOT_REQUIRED.
        """
        # Load all candidate entities
        if entity_type is not None:
            all_entities = self._entity_repo.list_entities(entity_type=entity_type)
        else:
            all_entities = self._entity_repo.list_entities()

        if not all_entities:
            logger.debug("QueryEntityResolver: repository is empty → UNRESOLVED")
            return EntityResolutionResult(
                status=EntityResolutionStatus.UNRESOLVED,
                extracted_name=None,
            )

        # Build a normalized lookup: norm_name → list[entity_id]
        norm_to_entities: dict[str, list[tuple[str, str, str]]] = {}
        # tuple: (entity_id, canonical_name, matched_form)
        for entity in all_entities:
            forms: list[str] = [entity.canonical_name] + entity.aliases
            for form in forms:
                norm = _normalize_name(form)
                if norm not in norm_to_entities:
                    norm_to_entities[norm] = []
                norm_to_entities[norm].append(
                    (entity.entity_id, entity.canonical_name, form)
                )

        # Extract candidate name substrings from the question
        candidate_names = _extract_candidate_names(question)

        # Try each candidate name against entity forms
        # Track (matched_name, entity_id, canonical_name) matches
        matched: list[tuple[str, str, str]] = []  # (matched_name, entity_id, canonical_name)

        for cand in candidate_names:
            norm_cand = _normalize_name(cand)
            if norm_cand in norm_to_entities:
                # Deduplicate by entity_id
                for eid, cname, form in norm_to_entities[norm_cand]:
                    if not any(m[1] == eid for m in matched):
                        matched.append((cand, eid, cname))

        if not matched:
            logger.debug(
                "QueryEntityResolver: no entity matched for question=%r → UNRESOLVED",
                question[:80],
            )
            return EntityResolutionResult(
                status=EntityResolutionStatus.UNRESOLVED,
                extracted_name=candidate_names[0] if candidate_names else None,
            )

        if len(matched) == 1:
            matched_name, entity_id, canonical_name = matched[0]
            logger.debug(
                "QueryEntityResolver: RESOLVED entity_id=%s name=%r",
                entity_id,
                canonical_name,
            )
            return EntityResolutionResult(
                status=EntityResolutionStatus.RESOLVED,
                entity_id=entity_id,
                entity_name=canonical_name,
                extracted_name=matched_name,
            )

        # Multiple matches — AMBIGUOUS
        # Deduplicate by entity_id, sort for determinism
        seen_ids: set[str] = set()
        unique_matches: list[tuple[str, str, str]] = []
        for item in sorted(matched, key=lambda x: x[1]):
            if item[1] not in seen_ids:
                seen_ids.add(item[1])
                unique_matches.append(item)

        if len(unique_matches) == 1:
            # Only one unique entity after dedup
            matched_name, entity_id, canonical_name = unique_matches[0]
            return EntityResolutionResult(
                status=EntityResolutionStatus.RESOLVED,
                entity_id=entity_id,
                entity_name=canonical_name,
                extracted_name=matched_name,
            )

        candidate_ids = [m[1] for m in unique_matches]
        candidate_names_list = [m[2] for m in unique_matches]
        logger.debug(
            "QueryEntityResolver: AMBIGUOUS %d entities matched: %s",
            len(unique_matches),
            candidate_ids,
        )
        return EntityResolutionResult(
            status=EntityResolutionStatus.AMBIGUOUS,
            candidates=candidate_ids,
            candidate_names=candidate_names_list,
            extracted_name=matched[0][0],
        )

    def resolve_by_id(self, entity_id: str) -> EntityResolutionResult:
        """Resolve by explicit entity_id (caller already knows the entity).

        Returns RESOLVED if found, UNRESOLVED if not found.
        """
        entity = self._entity_repo.get_by_id(entity_id)
        if entity is None:
            return EntityResolutionResult(
                status=EntityResolutionStatus.UNRESOLVED,
                extracted_name=entity_id,
            )
        return EntityResolutionResult(
            status=EntityResolutionStatus.RESOLVED,
            entity_id=entity.entity_id,
            entity_name=entity.canonical_name,
        )
