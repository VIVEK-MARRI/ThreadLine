"""Abstract base and concrete implementation for state interpretation.

A state interpreter answers:

    "Given a piece of evidence text from a meeting observation,
     what lifecycle state does it imply?"

This is Stage 1 of the Temporal State Engine pipeline:

    EntityObservation (source_text)
          ↓
    State Interpretation   ("What state does this evidence imply?")  ← HERE
          ↓
    Transition Policy      ("Is this a valid state change?")
          ↓
    StateObservation       (enriched with interpreted state)

Design mirrors app/entity_resolution/base.py and resolution_policy.py:
  - The interface is declared here (AbstractStateInterpreter).
  - The concrete implementation (KeywordStateInterpreter) is also here.
  - The service layer depends only on AbstractStateInterpreter so future
    implementations (embedding-based, LLM-based) are swappable.

Approach: Option C — Deterministic keyword-based rules.

The current extraction pipeline does not produce structured status fields
on mentions.  EntityMention.source_text is free text from the transcript.
A deterministic keyword-based interpreter is the correct approach without
introducing an LLM or network dependency.

Keyword priority order (first match wins):
  1. RESOLVED:    resolved, fixed, closed, completed, done, finished
  2. BLOCKED:     blocked, blocker, stuck, stalled, waiting
  3. IN_PROGRESS: in progress, in-progress, working on, started, underway, ongoing
  4. OPEN:        opened, raised, identified, reported, created, new issue, filed
  5. UNKNOWN:     (default — no keyword matched)

Known limitations
-----------------
- Cannot understand negation ("not blocked" still triggers BLOCKED).
- Cannot understand conditional language ("if this is not resolved").
- Only source_text is inspected — never raw meeting transcripts.
- Vocabulary is small and explicit.  No fuzzy or phonetic matching.
- Designed for replacement by a more sophisticated interpreter in future stages.
"""

import logging
from abc import ABC, abstractmethod

from app.models.temporal import TemporalState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Keyword vocabulary (deterministic — do NOT sort or reorder these)
# ---------------------------------------------------------------------------

# Each tuple: (priority_label, keywords_to_match, resulting_state).
# Rules are evaluated top-to-bottom; first match wins.
# Keywords are matched as case-insensitive substrings of the evidence text.
_KEYWORD_RULES: list[tuple[str, list[str], TemporalState]] = [
    (
        "RESOLVED",
        ["resolved", "fixed", "closed", "completed", "done", "finished"],
        TemporalState.RESOLVED,
    ),
    (
        "BLOCKED",
        ["blocked", "blocker", "stuck", "stalled", "waiting"],
        TemporalState.BLOCKED,
    ),
    (
        "IN_PROGRESS",
        ["in progress", "in-progress", "working on", "started", "underway", "ongoing"],
        TemporalState.IN_PROGRESS,
    ),
    (
        "OPEN",
        ["opened", "raised", "identified", "reported", "created", "new issue", "filed"],
        TemporalState.OPEN,
    ),
]


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class AbstractStateInterpreter(ABC):
    """Interface for state interpretation strategies.

    Implementors receive a piece of evidence text (typically the source_text
    of an EntityMention) and must return the TemporalState it implies.

    Contract
    --------
    - MUST return a deterministic TemporalState for identical inputs.
    - MUST NOT perform any network calls.
    - MUST NOT use an LLM.
    - MUST NOT modify any domain models.
    - SHOULD return TemporalState.UNKNOWN when evidence is insufficient to
      determine a specific state.
    """

    @abstractmethod
    def interpret(self, evidence_text: str) -> TemporalState:
        """Interpret the lifecycle state implied by *evidence_text*.

        Parameters
        ----------
        evidence_text:
            The surrounding transcript excerpt from an EntityMention.
            Typically the source_text field of an EntityMention.

        Returns
        -------
        TemporalState
            The interpreted state.  TemporalState.UNKNOWN when no
            state-bearing evidence is found.
        """
        ...


# ---------------------------------------------------------------------------
# Concrete implementation
# ---------------------------------------------------------------------------

class KeywordStateInterpreter(AbstractStateInterpreter):
    """Deterministic keyword-based state interpreter.

    Scans evidence_text (case-insensitive substring search) against a fixed,
    prioritised vocabulary.  The first matching rule wins.

    Priority order (highest to lowest):
      1. RESOLVED    — resolved, fixed, closed, completed, done, finished
      2. BLOCKED     — blocked, blocker, stuck, stalled, waiting
      3. IN_PROGRESS — in progress, in-progress, working on, started, underway, ongoing
      4. OPEN        — opened, raised, identified, reported, created, new issue, filed
      5. UNKNOWN     — (default: no keyword matched)

    Design notes
    ------------
    - Case-insensitive substring matching: "Started" matches "started".
    - No fuzzy matching.  "starte" does NOT match "started".
    - Negation is not handled: "not blocked" still triggers BLOCKED.
    - Multi-word keywords ("in progress") are matched as a whole.
    - Empty or whitespace-only evidence_text always returns UNKNOWN.

    Known limitations
    -----------------
    - Cannot understand negation or conditional language.
    - The vocabulary is intentionally small; false positives are possible
      in edge cases (e.g., "We are waiting for the resolved version").
    - Designed for replacement by an embedding or LLM-based interpreter.
    """

    def interpret(self, evidence_text: str) -> TemporalState:
        """Return the TemporalState implied by *evidence_text*.

        Applies keyword rules in priority order (RESOLVED → BLOCKED →
        IN_PROGRESS → OPEN → UNKNOWN).  First match wins.
        """
        if not evidence_text or not evidence_text.strip():
            logger.debug(
                "KeywordStateInterpreter: empty evidence_text → UNKNOWN."
            )
            return TemporalState.UNKNOWN

        lowered = evidence_text.lower()

        for rule_label, keywords, state in _KEYWORD_RULES:
            for kw in keywords:
                if kw in lowered:
                    logger.debug(
                        "KeywordStateInterpreter: matched keyword %r "
                        "(rule %s) → %s.",
                        kw,
                        rule_label,
                        state.value,
                    )
                    return state

        logger.debug(
            "KeywordStateInterpreter: no keyword matched → UNKNOWN."
        )
        return TemporalState.UNKNOWN
