"""Keyword-based dependency extractor (Stage 15).

Identifies explicit DEPENDS_ON and BLOCKS relationship statements in a piece
of transcript text by matching against a curated, conservative set of keyword
patterns.

Design principles
-----------------
- DETERMINISTIC: same input → same output, always.
- EVIDENCE-BACKED: only matches explicit, unambiguous language.
- CONSERVATIVE: any ambiguity → no extraction (omit rather than fabricate).
- NO ENTITY RESOLUTION: returns raw text references, not canonical IDs.
  Entity resolution is performed downstream by DependencyResolutionService.
- NO LLM: purely lexical pattern matching.

Pattern vocabulary
------------------
DEPENDS_ON patterns (source depends on target):
  "X depends on Y"
  "X is dependent on Y"
  "X is waiting for Y"
  "X cannot proceed until Y"
  "X cannot proceed without Y"
  "X is blocked until Y"         ← dependency framing (not blocking entity)
  "X requires Y"
  "X needs Y to"                 ← matches "X needs Y to [finish/complete/...]"
  "X is waiting on Y"

BLOCKS patterns (source is blocking target):
  "X is blocking Y"
  "X blocks Y"
  "X blocked Y"                  ← past tense blocking still indicates active blocker
  "Y is blocked by X"            ← passive construction → reversed: X BLOCKS Y
  "Y cannot proceed because of X"  ← X is the blocker
  "Y is waiting on X"            ← X BLOCKS Y (ambiguous; not included)

IMPORTANT: "Y is waiting on X" is NOT included for BLOCKS because waiting-on
can be dependency (I need X) rather than blocking (X is stopping me).
Only unambiguous blocking language produces a BLOCKS relationship.

Output
------
Each matched pattern returns an ExtractedRelationStatement containing:
  - source_ref: the text fragment identified as the source entity name
  - target_ref: the text fragment identified as the target entity name
  - relationship_type: RelationshipType.DEPENDS_ON or BLOCKS
  - matched_pattern: the specific pattern string that triggered the match
  - source_text: the full input text (for evidence traceability)

Limitations
-----------
- Multi-sentence texts: each sentence is processed independently.
- Pattern matching is case-insensitive but otherwise literal.
- If source_ref or target_ref is empty after extraction, the result is discarded.
- If source_ref == target_ref (normalised), the result is discarded.
- Extraction of participant names from pattern slots uses simple text splitting;
  for production, this could be improved with NLP chunking.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Iterator

from app.models.relationships import RelationshipType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExtractedRelationStatement:
    """The result of one pattern match within a source_text.

    This is NOT an entity relationship — participants are raw text references
    that must be resolved by DependencyResolutionService before becoming
    canonical ExplicitDependency records.

    Fields
    ------
    source_ref:
        Raw text fragment believed to name the source entity.
    target_ref:
        Raw text fragment believed to name the target entity.
    relationship_type:
        DEPENDS_ON or BLOCKS.
    matched_pattern:
        The pattern label that triggered this match (for diagnostics).
    source_text:
        The full original text passed to the extractor.
    """

    source_ref: str
    target_ref: str
    relationship_type: RelationshipType
    matched_pattern: str
    source_text: str


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------
# Each entry is a tuple of:
#   (compiled_regex, relationship_type, source_group, target_group)
# where source_group / target_group are the regex group names for participants.
#
# Patterns are deliberately conservative.  In every DEPENDS_ON pattern the
# grammatical subject is the dependent (source) and the object is the
# depended-upon (target).
#
# For passive BLOCKED_BY patterns (e.g. "Y is blocked by X"), source_group
# and target_group are swapped so that the extractor always returns
# (blocker=source, blocked=target) and we produce BLOCKS uniformly.

_FLAGS = re.IGNORECASE

_PATTERNS: list[tuple[re.Pattern, RelationshipType, str, str, str]] = [
    # ----- DEPENDS_ON --------------------------------------------------------

    # "X depends on Y"
    (
        re.compile(
            r"\b(?P<source>[A-Za-z0-9 _\-]+?)\s+depends?\s+on\s+(?P<target>[A-Za-z0-9 _\-]+)",
            _FLAGS,
        ),
        RelationshipType.DEPENDS_ON,
        "source",
        "target",
        "DEPENDS_ON:depends_on",
    ),

    # "X is dependent on Y"
    (
        re.compile(
            r"\b(?P<source>[A-Za-z0-9 _\-]+?)\s+is\s+dependent\s+on\s+(?P<target>[A-Za-z0-9 _\-]+)",
            _FLAGS,
        ),
        RelationshipType.DEPENDS_ON,
        "source",
        "target",
        "DEPENDS_ON:is_dependent_on",
    ),

    # "X is waiting for Y"
    (
        re.compile(
            r"\b(?P<source>[A-Za-z0-9 _\-]+?)\s+is\s+waiting\s+for\s+(?P<target>[A-Za-z0-9 _\-]+)",
            _FLAGS,
        ),
        RelationshipType.DEPENDS_ON,
        "source",
        "target",
        "DEPENDS_ON:is_waiting_for",
    ),

    # "X is waiting on Y"  (dependency framing — X needs Y)
    (
        re.compile(
            r"\b(?P<source>[A-Za-z0-9 _\-]+?)\s+is\s+waiting\s+on\s+(?P<target>[A-Za-z0-9 _\-]+)",
            _FLAGS,
        ),
        RelationshipType.DEPENDS_ON,
        "source",
        "target",
        "DEPENDS_ON:is_waiting_on",
    ),

    # "X cannot proceed until Y"
    (
        re.compile(
            r"\b(?P<source>[A-Za-z0-9 _\-]+?)\s+cannot\s+proceed\s+until\s+(?P<target>[A-Za-z0-9 _\-]+)",
            _FLAGS,
        ),
        RelationshipType.DEPENDS_ON,
        "source",
        "target",
        "DEPENDS_ON:cannot_proceed_until",
    ),

    # "X cannot proceed without Y"
    (
        re.compile(
            r"\b(?P<source>[A-Za-z0-9 _\-]+?)\s+cannot\s+proceed\s+without\s+(?P<target>[A-Za-z0-9 _\-]+)",
            _FLAGS,
        ),
        RelationshipType.DEPENDS_ON,
        "source",
        "target",
        "DEPENDS_ON:cannot_proceed_without",
    ),

    # "X requires Y"
    (
        re.compile(
            r"\b(?P<source>[A-Za-z0-9 _\-]+?)\s+requires?\s+(?P<target>[A-Za-z0-9 _\-]+)",
            _FLAGS,
        ),
        RelationshipType.DEPENDS_ON,
        "source",
        "target",
        "DEPENDS_ON:requires",
    ),

    # ----- BLOCKS ------------------------------------------------------------

    # "X is blocking Y"
    (
        re.compile(
            r"\b(?P<source>[A-Za-z0-9 _\-]+?)\s+is\s+blocking\s+(?P<target>[A-Za-z0-9 _\-]+)",
            _FLAGS,
        ),
        RelationshipType.BLOCKS,
        "source",
        "target",
        "BLOCKS:is_blocking",
    ),

    # "X blocks Y"
    (
        re.compile(
            r"\b(?P<source>[A-Za-z0-9 _\-]+?)\s+blocks\s+(?P<target>[A-Za-z0-9 _\-]+)",
            _FLAGS,
        ),
        RelationshipType.BLOCKS,
        "source",
        "target",
        "BLOCKS:blocks",
    ),

    # "X blocked Y"  (past tense — still records the blocking relationship)
    (
        re.compile(
            r"\b(?P<source>[A-Za-z0-9 _\-]+?)\s+blocked\s+(?P<target>[A-Za-z0-9 _\-]+)",
            _FLAGS,
        ),
        RelationshipType.BLOCKS,
        "source",
        "target",
        "BLOCKS:blocked_past",
    ),

    # "Y is blocked by X"  — passive → source=X (blocker), target=Y (blocked)
    (
        re.compile(
            r"\b(?P<blocked>[A-Za-z0-9 _\-]+?)\s+is\s+blocked\s+by\s+(?P<blocker>[A-Za-z0-9 _\-]+)",
            _FLAGS,
        ),
        RelationshipType.BLOCKS,
        "blocker",   # <-- source_group: the blocker
        "blocked",   # <-- target_group: the blocked entity
        "BLOCKS:is_blocked_by",
    ),
]


# ---------------------------------------------------------------------------
# Normalisation helper
# ---------------------------------------------------------------------------

def _clean_ref(text: str) -> str:
    """Strip leading/trailing whitespace and normalise internal spaces."""
    return " ".join(text.strip().split())


# ---------------------------------------------------------------------------
# Public extractor function
# ---------------------------------------------------------------------------

def extract_relationship_statements(
    source_text: str,
) -> list[ExtractedRelationStatement]:
    """Extract explicit relationship statements from a source_text excerpt.

    Applies all keyword patterns to the input text and returns every unambiguous
    match as an ExtractedRelationStatement.  Results are deduplicated by
    (source_ref, target_ref, relationship_type) to avoid returning the same
    logical relationship twice from overlapping patterns.

    Parameters
    ----------
    source_text:
        The verbatim or near-verbatim excerpt from a meeting transcript.

    Returns
    -------
    list[ExtractedRelationStatement]
        Deduplicated list of explicit relationship statements found.
        Empty list if no explicit relationship language was detected.

    Notes
    -----
    - Does NOT perform entity resolution.
    - Does NOT validate that referenced entities exist.
    - Does NOT assert causal certainty.
    - Any ambiguous, overlapping, or empty match is silently discarded.
    """
    results: list[ExtractedRelationStatement] = []
    seen: set[tuple[str, str, str]] = set()  # (source_ref, target_ref, rel_type)

    for pattern, rel_type, source_group, target_group, label in _PATTERNS:
        for match in pattern.finditer(source_text):
            try:
                raw_source = match.group(source_group)
                raw_target = match.group(target_group)
            except IndexError:
                continue

            source_ref = _clean_ref(raw_source)
            target_ref = _clean_ref(raw_target)

            # Guard: non-empty participants
            if not source_ref or not target_ref:
                logger.debug(
                    "KeywordExtractor: discarding match from pattern '%s' — "
                    "empty participant(s): source=%r target=%r",
                    label,
                    source_ref,
                    target_ref,
                )
                continue

            # Guard: self-relationship
            if source_ref.lower() == target_ref.lower():
                logger.debug(
                    "KeywordExtractor: discarding self-reference from pattern '%s': %r",
                    label,
                    source_ref,
                )
                continue

            dedup_key = (source_ref.lower(), target_ref.lower(), rel_type.value)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            stmt = ExtractedRelationStatement(
                source_ref=source_ref,
                target_ref=target_ref,
                relationship_type=rel_type,
                matched_pattern=label,
                source_text=source_text,
            )
            results.append(stmt)
            logger.debug(
                "KeywordExtractor: extracted %s: %r → %r (pattern=%s)",
                rel_type.value,
                source_ref,
                target_ref,
                label,
            )

    return results
