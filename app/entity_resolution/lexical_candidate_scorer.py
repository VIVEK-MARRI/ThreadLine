"""Deterministic, explainable lexical candidate scorer.

This is the first concrete implementation of AbstractCandidateScorer.
It uses simple, explainable lexical signals only: no embeddings, no
edit-distance, no LLMs, no external APIs.

Algorithm
---------

For each EntityCandidate, look up the corresponding CanonicalEntity
and evaluate the mention against:
  - the entity's canonical_name
  - each alias individually

Take the BEST score across all representations.

Per-representation scoring
--------------------------

Step 1 — Exact match check
    Normalise the mention text and the representation text.
    If they are equal → score = 1.0, stop.

Step 2 — Tokenise both sides (reuses lexical_utils.tokenize)
    mention_tokens      = tokenize(mention.text)
    representation_tokens = tokenize(representation_text)

Step 3 — Compute token overlap
    overlap = len(mention_tokens & representation_tokens)

Step 4 — Compute component scores
    If len(mention_tokens) == 0 or len(representation_tokens) == 0:
        mention_coverage = candidate_coverage = 0.0
    Else:
        mention_coverage   = overlap / len(mention_tokens)
        candidate_coverage = overlap / len(representation_tokens)

Step 5 — Combine
    score = clamp(
        WEIGHT_MENTION_COVERAGE   * mention_coverage
      + WEIGHT_CANDIDATE_COVERAGE * candidate_coverage,
        0.0, 1.0,
    )

Final score for a candidate
---------------------------
    best_score across all representations (canonical_name + aliases)

Final ordering (deterministic)
-------------------------------
    1. score descending
    2. canonical_name ascending
    3. entity_id ascending

Constants
---------
WEIGHT_MENTION_COVERAGE : float
    Weight applied to mention_coverage in the combined score formula.
    Currently 0.6.  Explains more of the score when the mention tokens
    are fully covered (recall-oriented signal).

WEIGHT_CANDIDATE_COVERAGE : float
    Weight applied to candidate_coverage in the combined score formula.
    Currently 0.4.  Penalises candidates whose representation is much
    larger than the mention (specificity-oriented signal).

SCORING_METHOD : str
    Identifier attached to every ScoredEntityCandidate produced by this
    scorer.  Kept as a constant so tests and the API can reference it
    without hard-coding strings.
"""

import logging
from typing import Optional

from app.entity_resolution.lexical_utils import tokenize
from app.entity_resolution.scoring_base import AbstractCandidateScorer
from app.models.entity import (
    CanonicalEntity,
    EntityCandidate,
    EntityMention,
    ScoredEntityCandidate,
)
from app.repositories.entity_repository import _normalize

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

WEIGHT_MENTION_COVERAGE: float = 0.6
"""Weight applied to mention_coverage in the combined score formula.

Recall-oriented: rewards candidates where every mention token is covered.
"""

WEIGHT_CANDIDATE_COVERAGE: float = 0.4
"""Weight applied to candidate_coverage in the combined score formula.

Specificity-oriented: penalises candidates whose representation is much
broader than the mention.
"""

SCORING_METHOD: str = "lexical_weighted_coverage"
"""Identifier label attached to every scored candidate this scorer produces."""

# Sanity-check: weights must sum to 1.0 so the max non-exact score equals 1.0.
assert abs(WEIGHT_MENTION_COVERAGE + WEIGHT_CANDIDATE_COVERAGE - 1.0) < 1e-9, (
    "WEIGHT_MENTION_COVERAGE + WEIGHT_CANDIDATE_COVERAGE must equal 1.0"
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float, hi: float) -> float:
    """Return *value* clamped to the closed interval [lo, hi]."""
    return max(lo, min(hi, value))


def _score_representation(
    mention_tokens: frozenset[str],
    normalized_mention: str,
    representation: str,
) -> tuple[float, float, float, bool]:
    """Score a single mention against one representation (name or alias).

    Parameters
    ----------
    mention_tokens:
        Pre-tokenised mention tokens (passed in to avoid re-tokenising).
    normalized_mention:
        The normalised mention text (for exact-match comparison).
    representation:
        A candidate name or alias to score against.

    Returns
    -------
    (score, mention_coverage, candidate_coverage, exact_match)
        All floats are in [0.0, 1.0].
    """
    normalized_rep = _normalize(representation)

    # Exact normalised match → maximum score, no formula needed.
    if normalized_mention == normalized_rep:
        return 1.0, 1.0, 1.0, True

    rep_tokens = tokenize(representation)

    # Handle empty token sets gracefully.
    if not mention_tokens or not rep_tokens:
        return 0.0, 0.0, 0.0, False

    overlap = len(mention_tokens & rep_tokens)
    mention_coverage = overlap / len(mention_tokens)
    candidate_coverage = overlap / len(rep_tokens)

    combined = (
        WEIGHT_MENTION_COVERAGE * mention_coverage
        + WEIGHT_CANDIDATE_COVERAGE * candidate_coverage
    )
    score = _clamp(combined, 0.0, 1.0)

    return score, mention_coverage, candidate_coverage, False


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------

class LexicalCandidateScorer(AbstractCandidateScorer):
    """Deterministic, explainable lexical scorer.

    Scores each candidate by computing weighted mention and candidate
    coverage across the entity's canonical_name and all aliases, then
    taking the best score across all representations.

    This scorer prioritises explainability: every score is decomposed into
    interpretable components (mention_coverage, candidate_coverage,
    exact_match, matched_representation).

    See module docstring for the full algorithm.
    """

    def score(
        self,
        mention: EntityMention,
        candidates: list[EntityCandidate],
        entities: list[CanonicalEntity],
    ) -> list[ScoredEntityCandidate]:
        """Score *candidates* for *mention* and return them in ranked order.

        Parameters
        ----------
        mention:
            The unresolved mention to score candidates for.
        candidates:
            The shortlist produced by a candidate generator.
        entities:
            The canonical entity objects corresponding to *candidates*.

        Returns
        -------
        list[ScoredEntityCandidate]
            Scored candidates ordered by (-score, canonical_name, entity_id).
            Empty when *candidates* is empty.
        """
        if not candidates:
            return []

        # Build a fast lookup: entity_id → CanonicalEntity.
        entity_map: dict[str, CanonicalEntity] = {e.entity_id: e for e in entities}

        # Pre-compute mention side (shared across all candidates).
        normalized_mention = _normalize(mention.text)
        mention_tokens = tokenize(mention.text)

        if not mention_tokens:
            # Every mention token was filtered out (e.g. single-character mention).
            # Return all candidates with score 0.0.
            logger.debug(
                "LexicalCandidateScorer: mention %r has no meaningful tokens — "
                "all candidates receive score=0.0.",
                mention.text,
            )
            results = [
                ScoredEntityCandidate(
                    entity_id=c.entity_id,
                    canonical_name=c.canonical_name,
                    score=0.0,
                    scoring_method=SCORING_METHOD,
                    matched_representation=c.canonical_name,
                    mention_coverage=0.0,
                    candidate_coverage=0.0,
                    exact_match=False,
                )
                for c in candidates
            ]
            results.sort(key=lambda s: (s.canonical_name, s.entity_id))
            return results

        scored: list[ScoredEntityCandidate] = []

        for candidate in candidates:
            entity = entity_map.get(candidate.entity_id)
            if entity is None:
                # Candidate entity not found in provided list — score 0.0.
                logger.warning(
                    "LexicalCandidateScorer: entity_id=%s not found in entity "
                    "list — scoring as 0.0.",
                    candidate.entity_id,
                )
                scored.append(
                    ScoredEntityCandidate(
                        entity_id=candidate.entity_id,
                        canonical_name=candidate.canonical_name,
                        score=0.0,
                        scoring_method=SCORING_METHOD,
                        matched_representation=candidate.canonical_name,
                        mention_coverage=0.0,
                        candidate_coverage=0.0,
                        exact_match=False,
                    )
                )
                continue

            # Evaluate against canonical_name and every alias independently;
            # take the best score to avoid dilution by unrelated aliases.
            representations: list[str] = [entity.canonical_name] + list(entity.aliases)

            best_score: float = -1.0
            best_mention_coverage: float = 0.0
            best_candidate_coverage: float = 0.0
            best_exact_match: bool = False
            best_representation: str = entity.canonical_name

            for rep in representations:
                s, mc, cc, em = _score_representation(
                    mention_tokens=mention_tokens,
                    normalized_mention=normalized_mention,
                    representation=rep,
                )
                if s > best_score:
                    best_score = s
                    best_mention_coverage = mc
                    best_candidate_coverage = cc
                    best_exact_match = em
                    best_representation = _normalize(rep)

                # Short-circuit: exact match is the maximum possible score.
                if em:
                    break

            logger.debug(
                "LexicalCandidateScorer: entity %r (id=%s) score=%.4f "
                "(mc=%.4f, cc=%.4f, exact=%s) matched_repr=%r for mention %r.",
                entity.canonical_name,
                entity.entity_id,
                best_score,
                best_mention_coverage,
                best_candidate_coverage,
                best_exact_match,
                best_representation,
                mention.text,
            )

            scored.append(
                ScoredEntityCandidate(
                    entity_id=candidate.entity_id,
                    canonical_name=candidate.canonical_name,
                    score=best_score,
                    scoring_method=SCORING_METHOD,
                    matched_representation=best_representation,
                    mention_coverage=best_mention_coverage,
                    candidate_coverage=best_candidate_coverage,
                    exact_match=best_exact_match,
                )
            )

        # Sort deterministically: score descending, then name ascending,
        # then entity_id ascending as a stable UUID tie-breaker.
        scored.sort(key=lambda s: (-s.score, s.canonical_name, s.entity_id))

        logger.info(
            "LexicalCandidateScorer: scored %d candidate(s) for mention %r.",
            len(scored),
            mention.text,
        )
        return scored
