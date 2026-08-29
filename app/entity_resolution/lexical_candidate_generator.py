"""Lexical candidate generator — token-overlap implementation.

This is the first concrete implementation of AbstractCandidateGenerator.
It uses simple, explainable lexical signals only: no embeddings, no
edit-distance, no LLMs, no external APIs.

Algorithm
---------

Step 1 — Normalize
    Apply the same _normalize() used by the exact-match resolver:
    strip → lowercase → collapse internal whitespace.

    Example: " Rahul Kumar " → "rahul kumar"

Step 2 — Tokenize
    Split on whitespace and discard tokens shorter than MIN_TOKEN_LENGTH
    characters.  The short-token guard prevents single-letter fragments
    (e.g. "r" from the alias "R. Kumar") from producing spurious matches.

    Example: "rahul kumar" → {"rahul", "kumar"}
    Example: "r. kumar"   → {"kumar"}   (short "r" discarded)

Step 3 — Build entity token sets
    For each canonical entity: collect tokens from canonical_name and every
    alias (all normalised + tokenised), then union them into one set.

    Example:
      canonical_name = "Rahul Kumar"  → {"rahul", "kumar"}
      alias          = "R. Kumar"     → {"kumar"}
      entity_tokens  = {"rahul", "kumar"}

Step 4 — Overlap
    overlap = |mention_tokens ∩ entity_tokens|

    An entity is a candidate when overlap ≥ 1.

    Example:
      mention  "Rahul"         → {"rahul"}
      entity   "Rahul Kumar"   → {"rahul", "kumar"}  overlap = 1 → candidate
      entity   "Rahul Sharma"  → {"rahul", "sharma"} overlap = 1 → candidate
      entity   "Ravi Kumar"    → {"ravi", "kumar"}   overlap = 0 → not a candidate

Step 5 — Order (deterministic)
    Sort by: (-overlap_count, canonical_name, entity_id)
      1. More overlapping tokens first (higher recall for the top result).
      2. Alphabetical canonical_name as a stable secondary key.
      3. entity_id as a final tie-breaker (UUID strings compare lexicographically).

    The overlap_count is used *only* for ordering and is never exposed to callers.

Constants
---------
MIN_TOKEN_LENGTH : int
    Minimum number of characters a token must have to participate in
    overlap scoring.  Currently 2.  Set to 1 to disable the guard.

CANDIDATE_REASON : str
    The reason label attached to every EntityCandidate produced by this
    generator.  Kept as a module-level constant so tests can reference it
    without hard-coding strings.
"""

import logging

from app.entity_resolution.base import AbstractCandidateGenerator
from app.entity_resolution.lexical_utils import MIN_TOKEN_LENGTH as MIN_TOKEN_LENGTH, tokenize as _tokenize_impl
from app.models.entity import CanonicalEntity, EntityCandidate, EntityMention

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# MIN_TOKEN_LENGTH is re-exported from lexical_utils (the import above already
# binds the name at module level, so existing callers can import it from here).

CANDIDATE_REASON: str = "lexical_token_overlap"
"""Reason label attached to every candidate produced by this generator."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> frozenset[str]:
    """Normalize *text* and return a frozenset of meaningful tokens.

    Delegates to :func:`app.entity_resolution.lexical_utils.tokenize` so the
    tokenisation contract is shared with the scorer.  This function is kept
    here (and re-exported) for backward compatibility with existing callers
    and tests that import it from this module.

    Parameters
    ----------
    text:
        Raw text to tokenize (e.g. a mention surface form or entity name).

    Returns
    -------
    frozenset[str]
        Set of lowercase, meaningful tokens.  Empty set if no token survives
        the length filter (e.g. input is a single-character string).
    """
    return _tokenize_impl(text)


def _entity_token_set(entity: CanonicalEntity) -> frozenset[str]:
    """Union of tokens from an entity's canonical_name and all aliases.

    Parameters
    ----------
    entity:
        The canonical entity to build a token set for.

    Returns
    -------
    frozenset[str]
        All meaningful tokens across the entity's name and aliases.
    """
    tokens: set[str] = set(_tokenize(entity.canonical_name))
    for alias in entity.aliases:
        tokens |= _tokenize(alias)
    return frozenset(tokens)


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------

class LexicalCandidateGenerator(AbstractCandidateGenerator):
    """Token-overlap candidate generator.

    Produces candidates by computing the lexical token overlap between a
    mention's surface form and each entity's canonical_name + aliases.

    This generator prioritises recall: it is acceptable to include entities
    that turn out not to be correct.  The scoring stage (not implemented
    today) is responsible for precision.

    See module docstring for the full algorithm.
    """

    def generate(
        self,
        mention: EntityMention,
        entities: list[CanonicalEntity],
    ) -> list[EntityCandidate]:
        """Return an ordered list of candidate entities for *mention*.

        The caller guarantees *entities* are already filtered to the same
        entity_type as *mention*.  This method does not re-filter by type.

        Parameters
        ----------
        mention:
            The mention to find candidates for.
        entities:
            Pre-filtered canonical entities of the same entity_type.

        Returns
        -------
        list[EntityCandidate]
            Candidates ordered by (-overlap_count, canonical_name, entity_id).
            Empty when no entity has at least one overlapping token.
        """
        mention_tokens = _tokenize(mention.text)

        if not mention_tokens:
            # Every token was filtered out (e.g. mention is a single character).
            # No meaningful comparison is possible — return empty list.
            logger.debug(
                "LexicalCandidateGenerator: mention %r has no meaningful tokens "
                "after tokenisation (all tokens below MIN_TOKEN_LENGTH=%d).",
                mention.text,
                MIN_TOKEN_LENGTH,
            )
            return []

        # Build (entity, overlap_count) pairs for entities with overlap ≥ 1.
        scored: list[tuple[CanonicalEntity, int]] = []
        for entity in entities:
            entity_tokens = _entity_token_set(entity)
            overlap = len(mention_tokens & entity_tokens)
            if overlap >= 1:
                scored.append((entity, overlap))
                logger.debug(
                    "LexicalCandidateGenerator: entity %r (id=%s) overlap=%d "
                    "with mention %r.",
                    entity.canonical_name,
                    entity.entity_id,
                    overlap,
                    mention.text,
                )

        if not scored:
            logger.debug(
                "LexicalCandidateGenerator: no candidates for mention %r "
                "(type=%s).",
                mention.text,
                mention.entity_type.value,
            )
            return []

        # Sort deterministically: most overlap first, then alphabetical name,
        # then entity_id as UUID-string tie-breaker.
        scored.sort(key=lambda pair: (-pair[1], pair[0].canonical_name, pair[0].entity_id))

        candidates = [
            EntityCandidate(
                entity_id=entity.entity_id,
                entity_type=entity.entity_type,
                canonical_name=entity.canonical_name,
                candidate_reason=CANDIDATE_REASON,
            )
            for entity, _ in scored
        ]

        logger.info(
            "LexicalCandidateGenerator: %d candidate(s) for mention %r (type=%s).",
            len(candidates),
            mention.text,
            mention.entity_type.value,
        )
        return candidates
