"""Abstract base and concrete implementation for temporal transition policies.

A temporal transition policy answers:

    "Given the current lifecycle state and a newly interpreted state from
     a new observation, is this a valid transition, and what is the
     resulting state?"

This is Stage 2 of the Temporal State Engine pipeline:

    State Interpretation   ("What state does this evidence imply?")
          ↓
    Transition Policy      ("Is this a valid state change?")  ← HERE
          ↓
    StateObservation       (with transition_occurred, from_state, to_state)

Design mirrors app/entity_resolution/resolution_policy.py:
  - The interface is declared here (AbstractTemporalStatePolicy).
  - The concrete implementation (DefaultTransitionPolicy) is also here.
  - The service layer depends only on AbstractTemporalStatePolicy so future
    policies are swappable.

Valid Transition Table (DefaultTransitionPolicy)
------------------------------------------------
UNKNOWN     → OPEN, IN_PROGRESS, BLOCKED, RESOLVED  (any — no prior info)
OPEN        → IN_PROGRESS, BLOCKED, RESOLVED
IN_PROGRESS → BLOCKED, RESOLVED
BLOCKED     → IN_PROGRESS, RESOLVED
RESOLVED    → (none — terminal state)

Invalid Transition Handling
----------------------------
When a transition is invalid (e.g., RESOLVED → IN_PROGRESS), the policy:
  - Returns is_valid=False with a reason string.
  - Does NOT raise an exception.
  - Leaves the current_state unchanged (caller responsibility).
  - The observation is still recorded in the timeline with is_valid_transition=False.

This is deterministic, auditable, and does not silently lose evidence.

Repeated State Handling
------------------------
If new_state == current_state, the policy returns a result indicating
no transition occurred (transition_occurred=False).  The observation is
still included in the timeline.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from app.models.temporal import TemporalState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Valid transition table
# ---------------------------------------------------------------------------

# Maps each current_state to the set of states it can legitimately transition to.
# RESOLVED has no valid outgoing transitions (terminal state).
_VALID_TRANSITIONS: dict[TemporalState, frozenset[TemporalState]] = {
    TemporalState.UNKNOWN: frozenset({
        TemporalState.OPEN,
        TemporalState.IN_PROGRESS,
        TemporalState.BLOCKED,
        TemporalState.RESOLVED,
    }),
    TemporalState.OPEN: frozenset({
        TemporalState.IN_PROGRESS,
        TemporalState.BLOCKED,
        TemporalState.RESOLVED,
    }),
    TemporalState.IN_PROGRESS: frozenset({
        TemporalState.BLOCKED,
        TemporalState.RESOLVED,
    }),
    TemporalState.BLOCKED: frozenset({
        TemporalState.IN_PROGRESS,
        TemporalState.RESOLVED,
    }),
    TemporalState.RESOLVED: frozenset(),  # terminal — no valid outgoing transitions
}


# ---------------------------------------------------------------------------
# Policy result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TransitionResult:
    """The outcome of evaluating a single state transition.

    Fields
    ------
    current_state:
        The state after the transition is applied (unchanged if invalid/repeated).
    transition_occurred:
        True when the state actually changed (valid transition, new_state != current).
    is_valid:
        True when the transition from previous_state to new_state is permitted.
        False for invalid transitions (previous_state unchanged).
    reason:
        Human-readable explanation.  Non-None whenever is_valid is False.
    """

    current_state: TemporalState
    transition_occurred: bool
    is_valid: bool
    reason: Optional[str]


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class AbstractTemporalStatePolicy(ABC):
    """Interface for temporal state transition policies.

    Implementors receive the current lifecycle state and a newly interpreted
    state, and must return a TransitionResult describing the outcome.

    Contract
    --------
    - MUST return a deterministic TransitionResult for identical inputs.
    - MUST NOT modify any domain models.
    - MUST NOT raise exceptions for invalid transitions.
    - MUST NOT perform network calls or use an LLM.
    - Invalid transitions MUST be recorded (is_valid=False) rather than silently ignored.
    - Repeated state (new == current) MUST return transition_occurred=False.
    """

    @abstractmethod
    def apply(
        self,
        current_state: TemporalState,
        new_state: TemporalState,
    ) -> TransitionResult:
        """Evaluate whether transitioning from *current_state* to *new_state* is valid.

        Parameters
        ----------
        current_state:
            The state before this observation is processed.
        new_state:
            The state interpreted from the new observation's evidence text.

        Returns
        -------
        TransitionResult
            An immutable result object describing the outcome.
        """
        ...


# ---------------------------------------------------------------------------
# Concrete implementation
# ---------------------------------------------------------------------------

class DefaultTransitionPolicy(AbstractTemporalStatePolicy):
    """Deterministic lifecycle transition policy for Threadline.

    Implements the valid transition table:
      UNKNOWN     → OPEN, IN_PROGRESS, BLOCKED, RESOLVED
      OPEN        → IN_PROGRESS, BLOCKED, RESOLVED
      IN_PROGRESS → BLOCKED, RESOLVED
      BLOCKED     → IN_PROGRESS, RESOLVED
      RESOLVED    → (no valid outgoing transitions)

    Decision cases (A–D)
    --------------------
    CASE A — new_state is UNKNOWN:
        No information gained.  Treat as a no-op (no transition).
        Returns transition_occurred=False, current_state unchanged.

    CASE B — new_state == current_state (repeated state):
        No transition needed.
        Returns transition_occurred=False, is_valid=True.

    CASE C — valid transition (new_state in allowed set for current_state):
        Returns transition_occurred=True, current_state=new_state, is_valid=True.

    CASE D — invalid transition (new_state NOT in allowed set):
        Returns transition_occurred=False, current_state unchanged, is_valid=False,
        reason set to a human-readable explanation.
    """

    def apply(
        self,
        current_state: TemporalState,
        new_state: TemporalState,
    ) -> TransitionResult:
        """Apply the transition policy and return an explainable result.

        See class docstring for Cases A–D.
        """
        # ------------------------------------------------------------------
        # CASE A — new_state is UNKNOWN: no information gained.
        # ------------------------------------------------------------------
        if new_state == TemporalState.UNKNOWN:
            logger.debug(
                "DefaultTransitionPolicy: CASE A — new_state is UNKNOWN "
                "(no information), current_state remains %s.",
                current_state.value,
            )
            return TransitionResult(
                current_state=current_state,
                transition_occurred=False,
                is_valid=True,
                reason=None,
            )

        # ------------------------------------------------------------------
        # CASE B — repeated state: no transition needed.
        # ------------------------------------------------------------------
        if new_state == current_state:
            logger.debug(
                "DefaultTransitionPolicy: CASE B — repeated state %s "
                "(no transition).",
                current_state.value,
            )
            return TransitionResult(
                current_state=current_state,
                transition_occurred=False,
                is_valid=True,
                reason=None,
            )

        # ------------------------------------------------------------------
        # CASE C — valid transition.
        # ------------------------------------------------------------------
        allowed = _VALID_TRANSITIONS.get(current_state, frozenset())
        if new_state in allowed:
            logger.debug(
                "DefaultTransitionPolicy: CASE C — valid transition "
                "%s → %s.",
                current_state.value,
                new_state.value,
            )
            return TransitionResult(
                current_state=new_state,
                transition_occurred=True,
                is_valid=True,
                reason=None,
            )

        # ------------------------------------------------------------------
        # CASE D — invalid transition: record but do not apply.
        # ------------------------------------------------------------------
        reason = (
            f"Transition {current_state.value} -> {new_state.value} is not "
            f"permitted by the transition policy."
        )
        logger.debug(
            "DefaultTransitionPolicy: CASE D — invalid transition "
            "%s → %s: %s",
            current_state.value,
            new_state.value,
            reason,
        )
        return TransitionResult(
            current_state=current_state,
            transition_occurred=False,
            is_valid=False,
            reason=reason,
        )
