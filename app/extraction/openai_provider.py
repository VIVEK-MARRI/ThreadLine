"""OpenAI extraction provider.

Uses the openai Python SDK with JSON mode to obtain a structured extraction
result from GPT-4o.  Threadline always validates the response against its
own Pydantic schemas regardless of what the provider returns.

Configuration (via environment variables or .env file):
  OPENAI_API_KEY   — required; your OpenAI secret key.
  OPENAI_MODEL     — optional; defaults to "gpt-4o".

Error handling:
  - Missing API key  → ExtractionProviderNotConfiguredError (HTTP 503)
  - API / network error → ExtractionError (HTTP 503)
  - Bad JSON / schema mismatch → ExtractionProviderResponseError (HTTP 502)
"""

import json
import logging
from datetime import datetime, timezone

from app.core.config import settings
from app.extraction.base import (
    AbstractExtractionProvider,
    ExtractionError,
    ExtractionProviderNotConfiguredError,
    ExtractionProviderResponseError,
)
from app.extraction.prompts import EXTRACTION_SYSTEM_PROMPT, build_extraction_user_message
from app.models.extraction import (
    Decision,
    Evidence,
    ExtractionResult,
    Issue,
    Risk,
    Task,
)

logger = logging.getLogger(__name__)


def _parse_extraction_payload(raw: dict, meeting_id: str) -> ExtractionResult:
    """Convert the raw JSON dict from the model into a validated ExtractionResult.

    Raises ExtractionProviderResponseError if the payload cannot be mapped
    to Threadline's schema.
    """
    try:
        issues = [
            Issue(
                description=item["description"],
                evidence=Evidence(source_text=item["evidence"]["source_text"]),
            )
            for item in raw.get("issues", [])
        ]
        tasks = [
            Task(
                description=item["description"],
                owner=item.get("owner"),
                deadline=item.get("deadline"),
                evidence=Evidence(source_text=item["evidence"]["source_text"]),
            )
            for item in raw.get("tasks", [])
        ]
        decisions = [
            Decision(
                description=item["description"],
                evidence=Evidence(source_text=item["evidence"]["source_text"]),
            )
            for item in raw.get("decisions", [])
        ]
        risks = [
            Risk(
                description=item["description"],
                severity=item.get("severity"),
                evidence=Evidence(source_text=item["evidence"]["source_text"]),
            )
            for item in raw.get("risks", [])
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise ExtractionProviderResponseError(
            f"Provider response did not match expected schema: {exc}"
        ) from exc

    return ExtractionResult(
        meeting_id=meeting_id,
        extracted_at=datetime.now(tz=timezone.utc),
        issues=issues,
        tasks=tasks,
        decisions=decisions,
        risks=risks,
    )


class OpenAIExtractionProvider(AbstractExtractionProvider):
    """Extraction provider backed by OpenAI's chat completions API.

    The client is instantiated lazily on the first call so that importing
    this module never raises even when the SDK is not installed or the key
    is absent — the error surfaces only when extraction is actually invoked.
    """

    def __init__(self, model: str | None = None) -> None:
        self._model = model or settings.openai_model
        self._client = None  # lazy init

    def _get_client(self):
        """Return a configured OpenAI client, raising clearly if misconfigured."""
        if self._client is not None:
            return self._client

        if not settings.openai_api_key:
            raise ExtractionProviderNotConfiguredError(
                "OPENAI_API_KEY is not configured.  "
                "Set it in your environment or .env file before calling the "
                "extraction endpoint.  "
                "To test without a real key, use the fake provider (see README)."
            )

        try:
            from openai import OpenAI  # type: ignore[import]
        except ImportError as exc:
            raise ExtractionProviderNotConfiguredError(
                "The 'openai' package is not installed.  "
                "Run: pip install openai"
            ) from exc

        self._client = OpenAI(api_key=settings.openai_api_key)
        return self._client

    def extract(self, transcript: str, meeting_id: str) -> ExtractionResult:
        """Call GPT-4o with JSON mode and return a validated ExtractionResult."""
        client = self._get_client()

        logger.info(
            "OpenAIExtractionProvider: requesting extraction for meeting=%s model=%s",
            meeting_id,
            self._model,
        )

        try:
            response = client.chat.completions.create(
                model=self._model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": build_extraction_user_message(transcript),
                    },
                ],
                temperature=0,  # deterministic extraction; no creative variance
            )
        except Exception as exc:
            # Catch all openai SDK errors (AuthenticationError, RateLimitError,
            # APIConnectionError, Timeout, etc.) and re-raise as ExtractionError
            # so the service layer doesn't need to import openai directly.
            logger.error(
                "OpenAI API call failed for meeting=%s: %s",
                meeting_id,
                exc,
                exc_info=True,
            )
            raise ExtractionError(
                f"OpenAI API call failed: {type(exc).__name__}"
            ) from exc

        raw_content = response.choices[0].message.content
        logger.debug("OpenAI raw response for meeting=%s: %s", meeting_id, raw_content)

        try:
            payload = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise ExtractionProviderResponseError(
                f"Provider returned non-JSON content: {exc}"
            ) from exc

        result = _parse_extraction_payload(payload, meeting_id)
        logger.info(
            "Extraction complete for meeting=%s: %d issues, %d tasks, %d decisions, %d risks",
            meeting_id,
            len(result.issues),
            len(result.tasks),
            len(result.decisions),
            len(result.risks),
        )
        return result
