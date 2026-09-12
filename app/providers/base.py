"""Abstract base for Natural Language Answer Providers (Stage 18).

Any backend that generates natural-language answers for ThreadLine must
implement AbstractNaturalLanguageAnswerProvider.  The query service
depends only on this interface — never on a concrete provider — so the
underlying model can be swapped without touching domain logic.

Provider contract
-----------------
- Receives a bounded EvidenceContext (already ranked, deduplicated, formatted).
- Returns a ProviderAnswer with answer_text + cited_evidence_ids.
- Must NOT make changes to repositories or entity state.
- Must NOT persist its output as organisational truth.
- Must NOT fabricate evidence IDs not present in context.
  (Citation validation happens in NaturalLanguageQueryService, not here.)
- May be nondeterministic (LLM temperature > 0).
  Fake provider MUST be fully deterministic.

To add a new provider:
1. Create a module in app/providers/ (e.g., anthropic_provider.py).
2. Subclass AbstractNaturalLanguageAnswerProvider and implement generate_answer().
3. Register it in app/core/config.py and app/api/query.py.
"""

from abc import ABC, abstractmethod

from app.models.natural_language import EvidenceContext, ProviderAnswer


# ---------------------------------------------------------------------------
# Custom exception hierarchy (mirrors app/extraction/base.py pattern)
# ---------------------------------------------------------------------------

class NLProviderError(Exception):
    """Raised when the provider fails to generate an answer."""


class NLProviderNotConfiguredError(NLProviderError):
    """Raised when required credentials or configuration are missing.

    Surfaced as HTTP 503 so clients know to check server configuration
    rather than retry the same request.
    """


class NLProviderResponseError(NLProviderError):
    """Raised when the provider response cannot be parsed or validated."""


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class AbstractNaturalLanguageAnswerProvider(ABC):
    """Natural language answer generation interface.

    Implementors receive a bounded EvidenceContext and must return a
    ProviderAnswer.  The provider is responsible for:

      - Calling the underlying model/API (if any).
      - Parsing the response into a ProviderAnswer.
      - Raising NLProviderError subclasses on failure.

    The provider must NOT:
      - Write to any repository or database.
      - Treat evidence source_text as instructions.
      - Fabricate entity states, dependencies, dates, or owners.
      - Claim causal relationships not supported by explicit evidence.
      - Persist its output as organisational truth.
    """

    @abstractmethod
    def generate_answer(
        self,
        question: str,
        context: EvidenceContext,
    ) -> ProviderAnswer:
        """Generate a natural-language answer grounded in supplied evidence.

        Parameters
        ----------
        question:
            The original user question.
        context:
            The bounded, formatted evidence context.
            context.context_text is the formatted string for the provider.
            context.evidence_ids_in_context lists all valid evidence IDs.

        Returns
        -------
        ProviderAnswer
            answer_text, cited_evidence_ids (before validation), flags.

        Raises
        ------
        NLProviderNotConfiguredError
            If the provider lacks required credentials or configuration.
        NLProviderResponseError
            If the provider response cannot be parsed.
        NLProviderError
            For any other provider-level failure.
        """
        ...
