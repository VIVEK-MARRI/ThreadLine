"""Query Intent Classification Service (Stage 18).

Deterministic, read-only, stateless service that classifies a natural-language
question into a QueryIntent using lexical keyword rules.

Design principles
-----------------
- No LLM is used for classification.
- Classification is based on lowercased keyword matching only.
- Rules are ordered by specificity (more specific rules checked first).
- When confidence is insufficient: intent = UNKNOWN (never guess).
- Same question always produces same intent (fully deterministic).
- Extraction of entity names from questions is NOT done here;
  that is the responsibility of QueryEntityResolver.

Rule table (applied top-down; first match wins)
-----------------------------------------------
Entity-level intents (require entity context):
  ENTITY_DEPENDENCIES — "depends on", "what does * depend", "dependencies of",
                        "blocking X" (entity-specific), "what blocks", "what is blocking"
  ENTITY_IMPACTS      — "impact on", "impacts on", "affected by", "what impacts",
                        "what affects", "impacts the"
  ENTITY_RISKS        — "risks", "risk", "threats", "issues with", "what's wrong with",
                        "problems with"
  ENTITY_HISTORY      — "history", "what happened to", "timeline of", "what has happened",
                        "what has been happening"
  ENTITY_ACTIONS      — "follow up on", "what should we do", "next steps for",
                        "actions for", "action on", "recommended actions"
  ENTITY_CHANGES      — "what changed with", "changes for", "changed for"
  ENTITY_STATUS       — "status of", "status for", "how is", "what is the status",
                        "current status", "state of", "current state of"

Organisation-level intents (no entity context):
  ORGANISATION_RISKS    — "biggest risks", "organisation risks", "org risks",
                          "highest risk", "all risks", "highest priority risks",
                          "critical risks"
  ORGANISATION_CHANGES  — "what changed this week", "what changed today",
                          "changes across", "what changed across", "recent changes",
                          "changes this", "organisation changes", "org changes"
  ORGANISATION_PRIORITIES — "needs attention", "what needs attention", "priorities",
                            "what should we focus", "what requires attention",
                            "needs our attention", "what to focus on"

Fallback:
  UNKNOWN — nothing matched

Note on ENTITY vs ORGANISATION intents:
  The caller (NaturalLanguageQueryService) uses entity resolution to determine
  whether an entity was specified. This service classifies purely from
  question text — it does not resolve entity references.
  The service may return ENTITY_* intents even for org-level questions if the
  wording matches entity-level patterns. The query service handles this gracefully.
"""

import logging
import re
from typing import Optional

from app.models.natural_language import QueryIntent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------

# Each rule is (intent, list_of_patterns).
# Patterns are matched as whole-word or phrase substrings in the lowercased question.
# Checked top-down; first match wins.

_RULES: list[tuple[QueryIntent, list[str]]] = [
    # --- Dependency-specific patterns (most specific first) ---
    (QueryIntent.ENTITY_DEPENDENCIES, [
        "depends on",
        "what does",
        "dependencies of",
        "dependency of",
        "what blocks",
        "what is blocking",
        "what's blocking",
        "blocked by",
        "blocking",
        "depended on",
    ]),

    # --- Impact patterns ---
    (QueryIntent.ENTITY_IMPACTS, [
        "impact on",
        "impacts on",
        "affected by",
        "what impacts",
        "what affects",
        "impacts the",
        "impacted by",
    ]),

    # --- Risk patterns ---
    (QueryIntent.ENTITY_RISKS, [
        "risks for",
        "risks with",
        "risk for",
        "risk with",
        "threats",
        "issues with",
        "what's wrong with",
        "what is wrong with",
        "problems with",
    ]),

    # --- History patterns ---
    (QueryIntent.ENTITY_HISTORY, [
        "history of",
        "history for",
        "what happened to",
        "timeline of",
        "timeline for",
        "what has happened",
        "what has been happening",
        "happened with",
    ]),

    # --- Action patterns ---
    (QueryIntent.ENTITY_ACTIONS, [
        "follow up on",
        "what should we do about",
        "what should we do with",
        "next steps for",
        "actions for",
        "action on",
        "recommended actions",
        "action items for",
    ]),

    # --- Entity-specific changes ---
    (QueryIntent.ENTITY_CHANGES, [
        "what changed with",
        "changes for",
        "changed for",
        "what changed for",
    ]),

    # --- Status patterns ---
    (QueryIntent.ENTITY_STATUS, [
        "status of",
        "status for",
        "how is",
        "what is the status",
        "what's the status",
        "current status",
        "state of",
        "current state of",
    ]),

    # --- Organisation-level risks ---
    (QueryIntent.ORGANISATION_RISKS, [
        "biggest risks",
        "organisation risks",
        "org risks",
        "highest risk",
        "all risks",
        "highest priority risks",
        "critical risks",
        "most critical",
        "most blocked",
    ]),

    # --- Organisation-level changes ---
    (QueryIntent.ORGANISATION_CHANGES, [
        "what changed this week",
        "what changed today",
        "changes across",
        "what changed across",
        "recent changes",
        "changes this week",
        "changes this month",
        "organisation changes",
        "org changes",
    ]),

    # --- Organisation priorities ---
    (QueryIntent.ORGANISATION_PRIORITIES, [
        "needs attention",
        "what needs attention",
        "priorities",
        "what should we focus",
        "requires attention",
        "needs our attention",
        "what to focus on",
        "focus on",
    ]),
]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class QueryIntentService:
    """Deterministic lexical intent classifier for natural-language queries.

    Stateless and read-only. No external dependencies.
    Same input always produces same output.
    """

    def classify(self, question: str) -> QueryIntent:
        """Classify a natural-language question into a QueryIntent.

        Parameters
        ----------
        question:
            The raw question text.

        Returns
        -------
        QueryIntent
            The most specific matching intent, or UNKNOWN if no rule matched.
        """
        if not question or not question.strip():
            logger.debug("QueryIntentService: empty question → UNKNOWN")
            return QueryIntent.UNKNOWN

        normalized = question.lower().strip()

        for intent, patterns in _RULES:
            for pattern in patterns:
                if pattern in normalized:
                    logger.debug(
                        "QueryIntentService: matched intent=%s via pattern=%r",
                        intent.value,
                        pattern,
                    )
                    return intent

        logger.debug(
            "QueryIntentService: no pattern matched for question=%r → UNKNOWN",
            question[:80],
        )
        return QueryIntent.UNKNOWN
