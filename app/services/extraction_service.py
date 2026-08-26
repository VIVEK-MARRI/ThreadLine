"""Extraction service layer.

Orchestrates information extraction for a stored meeting.  The service:
  - Knows about domain models, repositories, and the provider interface.
  - Has no awareness of HTTP, Pydantic API schemas, or storage mechanics.
  - Never directly imports any concrete LLM provider — it depends only on
    AbstractExtractionProvider, keeping the service testable and provider-agnostic.

Raised exceptions (caught and translated to HTTP errors by the API layer):
  - MeetingNotFoundError   → 404
  - ExtractionProviderNotConfiguredError → 503
  - ExtractionError        → 503
"""

import logging
from typing import Optional

from app.extraction.base import (
    AbstractExtractionProvider,
    ExtractionError,
    ExtractionProviderNotConfiguredError,
)
from app.models.extraction import ExtractionResult
from app.repositories.extraction_repository import AbstractExtractionRepository
from app.repositories.meeting_repository import AbstractMeetingRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Service-layer exception (not HTTP-aware)
# ---------------------------------------------------------------------------

class MeetingNotFoundError(Exception):
    """Raised when the requested meeting does not exist in the repository."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ExtractionService:
    """Encapsulates all extraction-related business operations."""

    def __init__(
        self,
        meeting_repository: AbstractMeetingRepository,
        extraction_repository: AbstractExtractionRepository,
        provider: AbstractExtractionProvider,
    ) -> None:
        self._meeting_repo = meeting_repository
        self._extraction_repo = extraction_repository
        self._provider = provider

    def extract_meeting(self, meeting_id: str) -> ExtractionResult:
        """Run information extraction on a stored meeting transcript.

        Steps
        -----
        1. Fetch the meeting from the meeting repository.
        2. Raise MeetingNotFoundError if it does not exist.
        3. Delegate extraction to the configured provider.
        4. Persist the result via the extraction repository.
        5. Return the validated ExtractionResult.

        Raises
        ------
        MeetingNotFoundError
            If no meeting with meeting_id exists.
        ExtractionProviderNotConfiguredError
            If the provider is not properly configured (e.g. missing API key).
        ExtractionError
            If the provider fails for any other reason.
        """
        meeting = self._meeting_repo.get_by_id(meeting_id)
        if meeting is None:
            raise MeetingNotFoundError(
                f"Meeting '{meeting_id}' not found.  "
                "Ingest it first via POST /api/v1/meetings before extracting."
            )

        logger.info("ExtractionService: starting extraction for meeting=%s", meeting_id)

        # Provider raises ExtractionError subclasses on failure — let them
        # propagate to the API layer which maps them to HTTP status codes.
        result = self._provider.extract(
            transcript=meeting.transcript,
            meeting_id=meeting_id,
        )

        self._extraction_repo.save(result)
        logger.info(
            "ExtractionService: saved extraction result for meeting=%s", meeting_id
        )
        return result

    def get_extraction_result(self, meeting_id: str) -> Optional[ExtractionResult]:
        """Return the most recent extraction result for a meeting, or None."""
        return self._extraction_repo.get_by_meeting_id(meeting_id)
