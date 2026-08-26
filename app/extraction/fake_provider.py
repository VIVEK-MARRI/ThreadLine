"""Fake / deterministic extraction provider for testing.

FakeExtractionProvider never makes network calls.  It returns a pre-canned
ExtractionResult supplied at construction time, making tests fully
deterministic and independent of any external API or credentials.

Usage in tests
--------------
    from app.extraction.fake_provider import FakeExtractionProvider
    from app.models.extraction import ExtractionResult, Issue, Evidence

    canned = ExtractionResult(
        meeting_id="test-id",
        extracted_at=datetime.now(tz=timezone.utc),
        issues=[Issue(description="...", evidence=Evidence(source_text="..."))],
        tasks=[],
        decisions=[],
        risks=[],
    )
    provider = FakeExtractionProvider(result=canned)
    result = provider.extract(transcript="anything", meeting_id="test-id")
    # result == canned

To simulate a provider failure:
    from app.extraction.base import ExtractionError
    provider = FakeExtractionProvider(result=canned, raise_on_extract=ExtractionError("boom"))
    provider.extract(...)  # raises ExtractionError
"""

from app.extraction.base import AbstractExtractionProvider, ExtractionError
from app.models.extraction import ExtractionResult


class FakeExtractionProvider(AbstractExtractionProvider):
    """Deterministic test double for AbstractExtractionProvider.

    Returns a fixed ExtractionResult or raises a pre-configured exception,
    enabling exhaustive unit testing without any real LLM calls.
    """

    def __init__(
        self,
        result: ExtractionResult,
        raise_on_extract: ExtractionError | None = None,
    ) -> None:
        """
        Parameters
        ----------
        result:
            The ExtractionResult to return from .extract().
        raise_on_extract:
            If provided, .extract() raises this exception instead of
            returning result.  Useful for testing error handling paths.
        """
        self._result = result
        self._raise = raise_on_extract

    def extract(self, transcript: str, meeting_id: str) -> ExtractionResult:
        """Return the canned result (or raise the configured exception)."""
        if self._raise is not None:
            raise self._raise
        return self._result
