"""Fake / deterministic natural language answer provider for testing (Stage 18).

FakeNaturalLanguageAnswerProvider never makes network calls.  It returns a
deterministic ProviderAnswer derived solely from the supplied EvidenceContext.

Behaviour
---------
- If context has no evidence items, sets insufficient_evidence=True.
- If insufficient_evidence_override is set at construction, always sets that flag.
- answer_text summarises the evidence count and lists cited IDs.
- cited_evidence_ids = all evidence_ids_in_context (cites all supplied evidence).
- Fully deterministic: same inputs always produce same output.
- Safe for CI: no network, no API key, no external dependencies.

Usage in tests
--------------
    from app.providers.fake_provider import FakeNaturalLanguageAnswerProvider

    provider = FakeNaturalLanguageAnswerProvider()
    answer = provider.generate_answer(question="...", context=context)
    # answer.answer_text is deterministic
    # answer.cited_evidence_ids == context.evidence_ids_in_context
    # answer.insufficient_evidence is False if evidence was present

To simulate insufficient evidence:
    provider = FakeNaturalLanguageAnswerProvider(insufficient_evidence_override=True)

To simulate a provider failure:
    provider = FakeNaturalLanguageAnswerProvider(raise_on_call=NLProviderError("boom"))
"""

from app.models.natural_language import EvidenceContext, EvidenceItem, ProviderAnswer
from app.providers.base import AbstractNaturalLanguageAnswerProvider, NLProviderError


class FakeNaturalLanguageAnswerProvider(AbstractNaturalLanguageAnswerProvider):
    """Deterministic test double for AbstractNaturalLanguageAnswerProvider.

    Returns a predictable ProviderAnswer from the supplied evidence context,
    enabling exhaustive unit testing without any LLM calls.
    """

    def __init__(
        self,
        insufficient_evidence_override: bool = False,
        raise_on_call: NLProviderError | None = None,
    ) -> None:
        """
        Parameters
        ----------
        insufficient_evidence_override:
            If True, always return insufficient_evidence=True regardless of context.
        raise_on_call:
            If provided, generate_answer raises this exception instead of returning.
        """
        self._insufficient = insufficient_evidence_override
        self._raise = raise_on_call

    def generate_answer(
        self,
        question: str,
        context: EvidenceContext,
    ) -> ProviderAnswer:
        """Return a deterministic answer from the evidence context.

        The answer text summarises the evidence count and lists the cited IDs.
        All supplied evidence IDs are cited (the citation validator in
        NaturalLanguageQueryService will accept all of them).

        This provider does NOT treat source_text as instructions.
        It only uses the structured evidence_ids_in_context list.
        """
        if self._raise is not None:
            raise self._raise

        if self._insufficient or not context.evidence_items:
            return ProviderAnswer(
                answer_text=(
                    "ThreadLine does not have sufficient evidence to answer this question. "
                    "No relevant intelligence signals were found."
                ),
                cited_evidence_ids=[],
                insufficient_evidence=True,
                warnings=[],
            )

        # Deterministic answer: summarise evidence types present.
        type_counts: dict[str, int] = {}
        for item in context.evidence_items:
            t = item.evidence_type.value
            type_counts[t] = type_counts.get(t, 0) + 1

        type_summary = ", ".join(
            f"{count} {t}" for t, count in sorted(type_counts.items())
        )
        n = len(context.evidence_items)
        answer_text = (
            f"Based on {n} evidence item(s) ({type_summary}), "
            f"ThreadLine has retrieved the following structured intelligence. "
            f"[Evidence: {', '.join(context.evidence_ids_in_context)}]"
        )

        return ProviderAnswer(
            answer_text=answer_text,
            cited_evidence_ids=list(context.evidence_ids_in_context),
            insufficient_evidence=False,
            warnings=[],
        )
