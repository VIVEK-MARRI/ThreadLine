"""Application configuration.

Centralises all runtime settings so they can be driven by environment
variables or a .env file later, without touching any other module.

Extraction settings
-------------------
OPENAI_API_KEY       Your OpenAI secret key.  Required when using the
                     OpenAI provider.  Leave unset during tests — the
                     fake provider does not need it.
OPENAI_MODEL         OpenAI model to use for extraction.
                     Defaults to "gpt-4o".
EXTRACTION_PROVIDER  Which provider to activate: "openai" (default) or
                     "fake" (returns an empty result — useful for smoke
                     tests without a real API key).
"""

from typing import Optional

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Threadline application settings."""

    app_name: str = "Threadline"
    app_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False

    # ------------------------------------------------------------------
    # LLM / Extraction settings
    # ------------------------------------------------------------------
    # openai_api_key is Optional so the application starts successfully
    # even when no key is configured.  The error surfaces at call time
    # with a clear ExtractionProviderNotConfiguredError.
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o"

    # Which extraction provider to use.  Recognised values: "openai", "fake".
    extraction_provider: str = "openai"

    # Which natural language provider to use. Recognised values: "openai", "fake".
    nl_provider: str = "fake"

    # Semantic index settings. The durable JSON backend is opt-in while the
    # rest of ThreadLine remains in-memory; tests keep the offline fallback.
    semantic_index_backend: str = "in_memory"
    semantic_index_path: str = ".threadline/semantic_index.json"
    embedding_provider: str = "fake"
    active_embedding_model: str = "fake"
    active_representation_version: str = "1.0"

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


# Module-level singleton – import this everywhere instead of instantiating Settings again.
settings = Settings()
