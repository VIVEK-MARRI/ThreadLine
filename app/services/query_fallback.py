"""Query routing fallback boundary (Stage 34, Part G).

Extension point
---------------
Intent classification → supported intent? YES → existing route.
NO (UNKNOWN) → fallback strategy probe → sufficient evidence? YES →
standard evidence→answer pipeline : NO → existing safe rejection.

Only the boundary and ONE conservative strategy ship in Stage 34.  No
semantic/LLM chatbot fallback: a fallback answer is produced exclusively
through the standard pipeline (context bounds + citation validation +
provider insufficient-evidence flag) over evidence that passes the
explicit gate below.

Conservative gate (LexicalSufficiencyFallback)
---------------------------------------------
An UNKNOWN question may attempt the pipeline only when the bounded,
revision-guarded, organisation-scoped semantic probe returns at least one
*valid* item, where valid means:

1. citable: non-blank evidence_id AND (entity_id OR meeting_id) — orphan
   or unattributed matches can never ground an answer;
2. lexically overlapping: the item's summary/source text shares at least
   MIN_OVERLAP_TOKENS distinct whole-word question tokens (length >=
   MIN_TOKEN_LENGTH) — a single common word ("should", "what") is never
   enough, and short/vague questions keep the safe rejection.

Rationale: similarity scores are relative rankings, not calibrated
relevance, so no score floor is invented; the lexical gate is
deterministic and matches the keyword classifier's philosophy.  Probe
failures (embedding/repo/shape errors) mean "insufficient evidence",
never a crash — mirroring the hybrid service's existing handling.
"""

import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime

from app.models.natural_language import EvidenceItem
from app.providers.embedding_base import EmbeddingError

logger = logging.getLogger(__name__)

MIN_OVERLAP_TOKENS = 2
MIN_TOKEN_LENGTH = 4
FALLBACK_PROBE_TOP_K = 10

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def question_tokens(question: str) -> set[str]:
    """Distinct whole-word tokens eligible for the overlap gate."""
    return {
        token
        for token in _TOKEN_RE.findall(question.lower())
        if len(token) >= MIN_TOKEN_LENGTH
    }


class AbstractQueryFallbackStrategy(ABC):
    """Sufficiency probe for UNKNOWN-intent questions (Stage 34 boundary)."""

    @abstractmethod
    def probe(
        self,
        question: str,
        current_time: datetime,
        max_items: int,
    ) -> list[EvidenceItem]:
        """Return citable, overlapping evidence, or [] when unsupported."""
        ...


class LexicalSufficiencyFallback(AbstractQueryFallbackStrategy):
    """Conservative UNKNOWN-intent fallback over the persisted semantic path."""

    def __init__(
        self,
        semantic_service,
        corpus_provider,
        current_revision_lookup=None,
        organisation_id: str | None = None,
    ) -> None:
        self._semantic = semantic_service
        self._corpus_provider = corpus_provider
        self._current_revision_lookup = current_revision_lookup
        self._organisation_id = organisation_id

    def probe(
        self,
        question: str,
        current_time: datetime,
        max_items: int,
    ) -> list[EvidenceItem]:
        tokens = question_tokens(question)
        if not tokens:
            return []
        try:
            corpus = {item.evidence_id: item for item in self._corpus_provider(current_time)}
            matches = self._semantic.search_persisted(
                query=question,
                source_lookup=corpus.get,
                top_k=min(max(1, max_items), FALLBACK_PROBE_TOP_K),
                current_revision_lookup=self._current_revision_lookup,
                organisation_id=self._organisation_id,
            )
        except (EmbeddingError, KeyError, ValueError, TypeError, AttributeError) as exc:
            # Probe failure = insufficient evidence, never a broken query.
            logger.warning("fallback semantic probe failed: %s", type(exc).__name__)
            return []
        valid: list[EvidenceItem] = []
        for match in matches:
            item = match.evidence
            if not _is_citable(item):
                continue
            haystack = f"{item.summary or ''}\n{item.source_text or ''}".lower()
            overlap = {token for token in tokens if _whole_word(token, haystack)}
            if len(overlap) >= MIN_OVERLAP_TOKENS:
                valid.append(item)
        return valid


def _is_citable(item: EvidenceItem) -> bool:
    return bool(item.evidence_id and item.evidence_id.strip()) and bool(
        item.entity_id or item.meeting_id
    )


def _whole_word(token: str, haystack: str) -> bool:
    return re.search(rf"\b{re.escape(token)}\b", haystack) is not None
