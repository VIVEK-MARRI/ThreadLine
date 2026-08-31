"""Abstract base and concrete implementation for resolution decision policies.

A resolution policy answers:

    \"Given scored candidates for an unresolved mention, is the evidence
     sufficient to make a safe, deterministic resolution decision?\"

This is Stage 4 of the entity-resolution pipeline:

    Entity Mention
          ↓
    Candidate Generation   (\"Who could this be?\")
          ↓
    Candidate Scoring      (\"How strong is the evidence for each?\")
          ↓
    Resolution Decision    (\"Do we have enough evidence to act?\")   ← HERE

Design mirrors app/entity_resolution/base.py and scoring_base.py:
  - The interface is declared here (AbstractResolutionPolicy).
  - The concrete implementation (ThresholdResolutionPolicy) is also here.
  - The service layer depends only on AbstractResolutionPolicy so future
    policies (ML-based, contextual, human-review) are swappable.

Separation of concerns
-----------------------
A SCORE IS NOT A DECISION.

The scoring stage ranks candidates by lexical similarity; it does NOT decide
whether the evidence is sufficient to resolve the mention.  That decision
belongs exclusively to the Resolution Policy.

The policy must be:
  DETERMINISTIC — same inputs always produce the same decision.
  EXPLAINABLE   — every decision includes a human-readable reason.
  SAFE          — the system abstains (AMBIGUOUS/UNRESOLVED) when uncertain.

Important: score values are lexical similarity scores in [0.0, 1.0].
They are NOT probabilities.  A score of 0.92 means the candidate received
a lexical similarity score of 0.92 under the scoring function.  It does NOT
mean there is a 92 % probability the entity is correct.
"""

import logging
from abc import ABC, abstractmethod

from app.models.entity import (
    ResolutionDecision,
    ResolutionOutcome,
    ScoredEntityCandidate,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Policy constants  (ThresholdResolutionPolicy defaults)
# ---------------------------------------------------------------------------

DEFAULT_RESOLUTION_THRESHOLD: float = 0.85
"""Minimum top-candidate score required before the engine will RESOLVE.

Chosen based on the lexical scoring scale used by LexicalCandidateScorer:
- Exact normalised match → 1.0.
- Strong partial overlap (e.g. first-name + last-name token overlap) → ~0.6–0.9.
- Weak overlap (single shared token) → ~0.3–0.6.

Raising this value makes the engine more conservative (fewer RESOLVED, more
UNRESOLVED).  Lowering it makes it more aggressive (more RESOLVED, risk of
incorrect merges).
"""

DEFAULT_AMBIGUITY_MARGIN: float = 0.10
"""Minimum score margin (top − second) required before the engine will RESOLVE.

When two candidates have similar scores, the engine abstains (AMBIGUOUS) rather
than picking the wrong one.  The margin must be at least this large for the top
candidate to be considered a clear winner.

Example:
    top_score    = 0.91
    second_score = 0.90
    margin       = 0.01  →  AMBIGUOUS  (margin < 0.10)

    top_score    = 0.94
    second_score = 0.40
    margin       = 0.54  →  RESOLVED   (margin ≥ 0.10)
"""


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class AbstractResolutionPolicy(ABC):
    """Interface for resolution decision strategies.

    Implementors receive a mention_id and the fully scored, ordered candidate
    list and must return a ResolutionDecision explaining what was decided.

    Contract
    --------
    - MUST return a deterministic ResolutionDecision for identical inputs.
    - MUST NOT mutate the scored candidates list.
    - MUST NOT create or persist canonical entities.
    - MUST NOT directly mutate any mention (the service layer does that).
    - A RESOLVED decision MUST select an entity_id from the candidate list.
    - AMBIGUOUS and UNRESOLVED decisions MUST have selected_entity_id = None.
    """

    @abstractmethod
    def decide(
        self,
        mention_id: str,
        scored_candidates: list[ScoredEntityCandidate],
    ) -> ResolutionDecision:
        """Apply the resolution policy and return an explainable decision.

        Parameters
        ----------
        mention_id:
            The ID of the mention being evaluated.
        scored_candidates:
            Scored, ordered candidate list from the scoring stage.
            The caller guarantees this is sorted by score descending
            (then canonical_name ascending, entity_id ascending).
            May be empty.

        Returns
        -------
        ResolutionDecision
            An explainable decision with outcome, selected_entity_id, scores,
            margin, and a human-readable reason.
        """
        ...


# ---------------------------------------------------------------------------
# Concrete implementation
# ---------------------------------------------------------------------------

class ThresholdResolutionPolicy(AbstractResolutionPolicy):
    """Deterministic threshold + margin resolution policy.

    Decision algorithm (Cases A–D)
    --------------------------------

    CASE A — No candidates:
        outcome          = UNRESOLVED
        selected_entity_id = None

    CASE B — Top score below the resolution threshold:
        if top_score < resolution_threshold:
            outcome          = UNRESOLVED
            selected_entity_id = None

    CASE C — Top score meets threshold but margin is too small:
        if top_score >= resolution_threshold
        and top_score - second_score < ambiguity_margin:
            outcome          = AMBIGUOUS
            selected_entity_id = None

    CASE D — Strong and clear winner:
        if top_score >= resolution_threshold
        and top_score - second_score >= ambiguity_margin:
            outcome          = RESOLVED
            selected_entity_id = top_candidate.entity_id

    Single-candidate policy
    -----------------------
    When there is only one candidate, there is no second candidate to compare
    against.  The margin is treated as positive-infinity — i.e., the single
    candidate is always considered a clear winner if it meets the threshold.
    This is safe because there is no ambiguity by definition.

    Parameters
    ----------
    resolution_threshold:
        Minimum top score required to resolve.  Defaults to
        DEFAULT_RESOLUTION_THRESHOLD (0.85).
    ambiguity_margin:
        Minimum gap between top and second score for a clear winner.
        Defaults to DEFAULT_AMBIGUITY_MARGIN (0.10).
    """

    def __init__(
        self,
        resolution_threshold: float = DEFAULT_RESOLUTION_THRESHOLD,
        ambiguity_margin: float = DEFAULT_AMBIGUITY_MARGIN,
    ) -> None:
        self._resolution_threshold = resolution_threshold
        self._ambiguity_margin = ambiguity_margin

    @property
    def resolution_threshold(self) -> float:
        """Minimum score required to resolve."""
        return self._resolution_threshold

    @property
    def ambiguity_margin(self) -> float:
        """Minimum margin between top and second score for a clear winner."""
        return self._ambiguity_margin

    def decide(
        self,
        mention_id: str,
        scored_candidates: list[ScoredEntityCandidate],
    ) -> ResolutionDecision:
        """Apply the threshold + margin policy and return an explainable decision.

        See class docstring for the full algorithm.
        """
        # ------------------------------------------------------------------
        # CASE A — No candidates at all.
        # ------------------------------------------------------------------
        if not scored_candidates:
            logger.info(
                "ThresholdResolutionPolicy: mention %s — CASE A: no candidates → UNRESOLVED.",
                mention_id,
            )
            return ResolutionDecision(
                mention_id=mention_id,
                outcome=ResolutionOutcome.UNRESOLVED,
                selected_entity_id=None,
                top_score=None,
                second_score=None,
                score_margin=None,
                reason="No candidates were generated for this mention.",
            )

        # Candidates are already sorted by score descending.
        top = scored_candidates[0]
        top_score = top.score
        second: ScoredEntityCandidate | None = (
            scored_candidates[1] if len(scored_candidates) > 1 else None
        )
        second_score: float | None = second.score if second is not None else None

        # Compute margin.
        # Single-candidate: no second score → margin is conceptually +∞,
        # represented as None to avoid fabricating a number.
        if second_score is not None:
            margin: float | None = top_score - second_score
        else:
            margin = None  # Single candidate — no second to compare against.

        # ------------------------------------------------------------------
        # CASE B — Top score below threshold.
        # ------------------------------------------------------------------
        if top_score < self._resolution_threshold:
            logger.info(
                "ThresholdResolutionPolicy: mention %s — CASE B: "
                "top_score=%.4f < threshold=%.4f → UNRESOLVED.",
                mention_id,
                top_score,
                self._resolution_threshold,
            )
            return ResolutionDecision(
                mention_id=mention_id,
                outcome=ResolutionOutcome.UNRESOLVED,
                selected_entity_id=None,
                top_score=top_score,
                second_score=second_score,
                score_margin=margin,
                reason=(
                    f"No candidate exceeded the confidence threshold "
                    f"(top score {top_score:.4f} < threshold {self._resolution_threshold:.4f})."
                ),
            )

        # From here on: top_score >= resolution_threshold.

        # ------------------------------------------------------------------
        # Single-candidate fast path (no ambiguity possible).
        # ------------------------------------------------------------------
        if second is None:
            logger.info(
                "ThresholdResolutionPolicy: mention %s — single candidate "
                "(top_score=%.4f >= threshold=%.4f) → RESOLVED (no second candidate).",
                mention_id,
                top_score,
                self._resolution_threshold,
            )
            return ResolutionDecision(
                mention_id=mention_id,
                outcome=ResolutionOutcome.RESOLVED,
                selected_entity_id=top.entity_id,
                top_score=top_score,
                second_score=None,
                score_margin=None,
                reason=(
                    f"Only one candidate was available and its score ({top_score:.4f}) "
                    f"exceeded the confidence threshold ({self._resolution_threshold:.4f})."
                ),
            )

        # ------------------------------------------------------------------
        # CASE C — High enough score but margin is too small.
        # ------------------------------------------------------------------
        assert margin is not None  # second is not None → margin is not None
        if margin < self._ambiguity_margin:
            logger.info(
                "ThresholdResolutionPolicy: mention %s — CASE C: "
                "top_score=%.4f >= threshold, margin=%.4f < ambiguity_margin=%.4f → AMBIGUOUS.",
                mention_id,
                top_score,
                margin,
                self._ambiguity_margin,
            )
            return ResolutionDecision(
                mention_id=mention_id,
                outcome=ResolutionOutcome.AMBIGUOUS,
                selected_entity_id=None,
                top_score=top_score,
                second_score=second_score,
                score_margin=margin,
                reason=(
                    f"Top candidate exceeded the confidence threshold "
                    f"({top_score:.4f} >= {self._resolution_threshold:.4f}) but was "
                    f"too close to the second candidate "
                    f"(margin {margin:.4f} < {self._ambiguity_margin:.4f})."
                ),
            )

        # ------------------------------------------------------------------
        # CASE D — Strong and clear winner.
        # ------------------------------------------------------------------
        logger.info(
            "ThresholdResolutionPolicy: mention %s — CASE D: "
            "top_score=%.4f >= threshold, margin=%.4f >= ambiguity_margin=%.4f → RESOLVED.",
            mention_id,
            top_score,
            margin,
            self._ambiguity_margin,
        )
        return ResolutionDecision(
            mention_id=mention_id,
            outcome=ResolutionOutcome.RESOLVED,
            selected_entity_id=top.entity_id,
            top_score=top_score,
            second_score=second_score,
            score_margin=margin,
            reason=(
                f"Top candidate exceeded the confidence threshold "
                f"({top_score:.4f} >= {self._resolution_threshold:.4f}) and had "
                f"sufficient margin over the second candidate "
                f"(margin {margin:.4f} >= {self._ambiguity_margin:.4f})."
            ),
        )
