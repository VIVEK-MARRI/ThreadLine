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
  "X is blocked until Y"         ← dependency framing (not a blocking entity)
  "X requires Y"
  "X needs Y"                    ← conservative; target must be a noun phrase

BLOCKS patterns (source is blocking target):
  "X is blocking Y"
  "X blocks Y"
  "X blocked Y"                  ← past tense; uses negative lookbehind to
                                    avoid misfiring on passive "is blocked by"
  "Y is blocked by X"            ← passive → reversed: X BLOCKS Y
  "Y is being blocked by X"      ← passive progressive → reversed: X BLOCKS Y

IMPORTANT: "Y is waiting on X" is NOT included for BLOCKS or DEPENDS_ON
because waiting-on can be either dependency (I need X) or polite language
(I am waiting on a call) — too ambiguous to classify safely.

Participant normalization
-------------------------
After regex extraction the raw text references are cleaned by
_normalize_participant() which:
  1. Strips leading/trailing whitespace.
  2. Removes common leading articles (the, a, an) — these are grammatical
     wrappers, not part of the entity name.
  3. Removes obvious trailing clauses that arise from greedy pattern captures:
     "to finish", "to complete", "to be done", "to proceed" etc.
  4. Removes trailing punctuation (., ;, !, ?).
  5. Collapses internal whitespace.
The function is CONSERVATIVE: it only removes elements that are clearly
grammatical. It never removes content words from multi-word entity names.

Output
------
Each matched pattern returns an ExtractedRelationStatement containing:
  - source_ref: the normalised text fragment for the source entity name
  - target_ref: the normalised text fragment for the target entity name
  - relationship_type: RelationshipType.DEPENDS_ON or BLOCKS
  - matched_pattern: the specific pattern string that triggered the match
  - source_text: the full input text (for evidence traceability)

Limitations
-----------
- Multi-sentence texts: each sentence is processed independently.
- Pattern matching is case-insensitive but otherwise literal.
- If source_ref or target_ref is empty after normalisation, the result is
  discarded.
- If source_ref == target_ref (normalised), the result is discarded.
"""

import logging
import re
from dataclasses import dataclass
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
        Normalised text fragment believed to name the source entity.
    target_ref:
        Normalised text fragment believed to name the target entity.
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
# Normalisation helpers
# ---------------------------------------------------------------------------

def _clean_ref(text: str) -> str:
    """Strip leading/trailing whitespace and normalise internal spaces."""
    return " ".join(text.strip().split())


# Compiled normalisation patterns (applied in order by _normalize_participant)
_RE_LEADING_ARTICLE = re.compile(
    r"^(?:the|a|an)\s+",
    re.IGNORECASE,
)

# Trailing clauses that commonly arise from greedy pattern captures.
# These start with words that signal a verbal continuation rather than
# naming a second entity.  Only strip when preceded by whitespace.
_RE_TRAILING_CLAUSE = re.compile(
    r"\s+(?:to\s+\w+.*|in\s+order\s+.*|so\s+that\s+.*|before\s+.*)$",
    re.IGNORECASE,
)

# Trailing sentence punctuation
_RE_TRAILING_PUNCT = re.compile(r"[.,;!?]+$")


def _normalize_participant(text: str) -> str:
    """Deterministically normalise an extracted participant text reference.

    Steps (applied in order):
    1. _clean_ref: strip whitespace, collapse internal spaces.
    2. Strip leading article: 'the', 'a', 'an'.
    3. Strip trailing clause: common verbal continuations captured by
       greedy patterns (e.g. "to finish", "in order to proceed").
    4. Strip trailing punctuation.
    5. Final clean_ref pass.

    This function is conservative — it only removes clearly grammatical
    wrappers.  Multi-word entity names like "Customer Support Service"
    are left intact.
    """
    result = _clean_ref(text)
    result = _RE_LEADING_ARTICLE.sub("", result)
    result = _RE_TRAILING_CLAUSE.sub("", result)
    result = _RE_TRAILING_PUNCT.sub("", result)
    result = _clean_ref(result)
    return result


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------
# Each entry is a tuple of:
#   (compiled_regex, relationship_type, source_group, target_group, label)
# where source_group / target_group are the regex group names for participants.
#
# Patterns are deliberately conservative.  In every DEPENDS_ON pattern the
# grammatical subject is the dependent (source) and the object is the
# depended-upon (target).
#
# For passive BLOCKED_BY patterns (e.g. "Y is blocked by X"), source_group
# and target_group are swapped so that the extractor always returns
# (blocker=source, blocked=target) and we produce BLOCKS uniformly.
#
# ORDERING MATTERS: more-specific patterns are listed before less-specific
# ones to avoid incorrect matches.  In particular, the passive "is blocked by"
# pattern is listed BEFORE the active "blocked" past-tense pattern.

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

    # "X is blocked until Y"  — dependency framing
    # Source cannot proceed until target (event/entity) completes.
    # This is NOT the same as "X is blocked by Y" — there is no single
    # blocking entity; rather X has a dependency on Y completing.
    (
        re.compile(
            r"\b(?P<source>[A-Za-z0-9 _\-]+?)\s+is\s+blocked\s+until\s+(?P<target>[A-Za-z0-9 _\-]+)",
            _FLAGS,
        ),
        RelationshipType.DEPENDS_ON,
        "source",
        "target",
        "DEPENDS_ON:is_blocked_until",
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

    # "X needs Y"
    # Conservative: "X needs help" will produce target="help" which will fail
    # entity resolution downstream, producing no confirmed relationship.
    # "X needs Y's approval" is captured as target="Y's approval" which will
    # similarly fail resolution — safe conservative behaviour.
    (
        re.compile(
            r"\b(?P<source>[A-Za-z0-9 _\-]+?)\s+needs?\s+(?P<target>[A-Za-z0-9 _\-]+)",
            _FLAGS,
        ),
        RelationshipType.DEPENDS_ON,
        "source",
        "target",
        "DEPENDS_ON:needs",
    ),

    # ----- BLOCKS ------------------------------------------------------------

    # "X is blocking Y"  — active progressive
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

    # "X blocks Y"  — active simple present
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

    # "Y is blocked by X"  — passive → source=X (blocker), target=Y (blocked)
    # This MUST come before the "X blocked Y" past-tense pattern so that the
    # specific passive form is recognised first.
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

    # "Y is being blocked by X"  — passive progressive → source=X (blocker)
    (
        re.compile(
            r"\b(?P<blocked>[A-Za-z0-9 _\-]+?)\s+is\s+being\s+blocked\s+by\s+(?P<blocker>[A-Za-z0-9 _\-]+)",
            _FLAGS,
        ),
        RelationshipType.BLOCKS,
        "blocker",   # <-- source_group: the blocker
        "blocked",   # <-- target_group: the blocked entity
        "BLOCKS:is_being_blocked_by",
    ),

    # "X blocked Y"  — active past tense
    # Uses a negative lookbehind (?<!is ) and (?<!being ) to prevent this
    # pattern from firing on passive constructions like "is blocked by" or
    # "is being blocked by" — those are handled by the patterns above.
    (
        re.compile(
            r"\b(?P<source>[A-Za-z0-9 _\-]+?)\s+(?<!is )(?<!being )blocked\s+(?!by\b)(?P<target>[A-Za-z0-9 _\-]+)",
            _FLAGS,
        ),
        RelationshipType.BLOCKS,
        "source",
        "target",
        "BLOCKS:blocked_past",
    ),
]


# ---------------------------------------------------------------------------
# Public extractor function
# ---------------------------------------------------------------------------

def extract_relationship_statements(
    source_text: str,
) -> list[ExtractedRelationStatement]:
    """Extract explicit relationship statements from a source_text excerpt.

    Applies all keyword patterns to the input text and returns every
    unambiguous match as an ExtractedRelationStatement.  Results are
    deduplicated by (source_ref, target_ref, relationship_type) to avoid
    returning the same logical relationship twice from overlapping patterns.

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

            source_ref = _normalize_participant(raw_source)
            target_ref = _normalize_participant(raw_target)

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
