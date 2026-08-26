"""Abstract base class for extraction providers.

Any LLM or model that powers Threadline's extraction pipeline must implement
AbstractExtractionProvider.  The extraction service depends only on this
interface — never on a concrete provider — so the underlying model can be
swapped without touching any domain logic.

To add a new provider:
1. Create a new module in app/extraction/ (e.g., gemini_provider.py).
2. Subclass AbstractExtractionProvider and implement extract().
3. Register it in app/core/config.py and app/api/meetings.py.
"""

from abc import ABC, abstractmethod

from app.models.extraction import ExtractionResult


# ---------------------------------------------------------------------------
# Custom exception types
# ---------------------------------------------------------------------------

class ExtractionError(Exception):
    """Raised when extraction fails for a recoverable or unrecoverable reason."""


class ExtractionProviderNotConfiguredError(ExtractionError):
    """Raised when required credentials / configuration are missing.

    Surfaced as a 503 Service Unavailable so clients know to check
    server configuration rather than retry the same request.
    """


class ExtractionProviderResponseError(ExtractionError):
    """Raised when the provider returns a response that cannot be parsed
    or validated against Threadline's extraction schema."""


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class AbstractExtractionProvider(ABC):
    """Extraction model interface.

    Implementors receive a raw transcript string and must return a fully
    populated, Threadline-validated ExtractionResult.  The provider is
    responsible for:

      - Calling the underlying model/API.
      - Parsing the raw response into Pydantic models.
      - Raising ExtractionError subclasses on failure.

    The provider must NOT:
      - Write to any repository or database.
      - Perform entity resolution or cross-meeting reasoning.
      - Infer or fabricate information not present in the transcript.
    """

    @abstractmethod
    def extract(self, transcript: str, meeting_id: str) -> ExtractionResult:
        """Extract structured facts from a meeting transcript.

        Parameters
        ----------
        transcript:
            The full text of the meeting transcript.
        meeting_id:
            The ID of the source meeting, used to populate ExtractionResult.

        Returns
        -------
        ExtractionResult
            A validated extraction result.

        Raises
        ------
        ExtractionProviderNotConfiguredError
            If the provider lacks required credentials or configuration.
        ExtractionProviderResponseError
            If the provider response cannot be parsed or validated.
        ExtractionError
            For any other provider-level failure (timeout, quota, etc.).
        """
        ...
