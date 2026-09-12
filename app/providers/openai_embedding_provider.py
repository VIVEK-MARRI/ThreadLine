"""OpenAI embedding provider (Stage 19).

Provides text embeddings via OpenAI's Embeddings API.

Configuration
---------
- API key from environment: OPENAI_API_KEY
- Model: configurable, default "text-embedding-3-small"
- Optional lazy import (network not required when using fake provider)

Usage
-----
When embedding_provider=openai in config:

    from app.providers.openai_embedding_provider import OpenAIEmbeddingProvider
    from app.core.config import get_config

    config = get_config()
    if config.embedding_provider == "openai":
        provider = OpenAIEmbeddingProvider(
            api_key=config.openai_api_key,
            model=config.embedding_model or "text-embedding-3-small"
        )
        vec = provider.embed_text("payment gateway")

Failure modes
---------
- Missing API key: raises EmbeddingProviderNotConfiguredError (HTTP 503)
- API error: raises EmbeddingError (HTTP 500)
- Invalid response: raises EmbeddingProviderResponseError (HTTP 500)

No network during tests
---------
FakeEmbeddingProvider is the default and requires no network.
OpenAI tests must mock the API or be marked @pytest.mark.skip.
"""

import os
from typing import Optional

from app.providers.embedding_base import (
    AbstractEmbeddingProvider,
    EmbeddingError,
    EmbeddingProviderNotConfiguredError,
    EmbeddingProviderResponseError,
)


class OpenAIEmbeddingProvider(AbstractEmbeddingProvider):
    """Text embeddings via OpenAI API.

    Lazy-imports the openai library to avoid requiring it when
    using the fake provider.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "text-embedding-3-small",
    ) -> None:
        """
        Parameters
        ----------
        api_key:
            OpenAI API key. If None, looks for OPENAI_API_KEY env var.
        model:
            Embedding model name. Default: "text-embedding-3-small".

        Raises
        ------
        EmbeddingProviderNotConfiguredError
            If no API key is available.
        """
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self._api_key:
            raise EmbeddingProviderNotConfiguredError(
                "OpenAI API key not provided and OPENAI_API_KEY not set in environment"
            )
        self._model = model
        self._client = None  # Lazy-loaded

    def _get_client(self):
        """Lazy-load the OpenAI client."""
        if self._client is None:
            try:
                import openai
            except ImportError:
                raise EmbeddingProviderNotConfiguredError(
                    "openai library not installed. "
                    "Install with: pip install openai"
                )
            self._client = openai.OpenAI(api_key=self._api_key)
        return self._client

    def embed_text(self, text: str) -> list[float]:
        """Get embedding from OpenAI API.

        Parameters
        ----------
        text:
            The text to embed.

        Returns
        -------
        list[float]
            Embedding vector.

        Raises
        ------
        EmbeddingError
            For API failures, timeouts, etc.
        EmbeddingProviderResponseError
            For invalid responses.
        """
        if not text:
            raise ValueError("text must not be empty")

        try:
            client = self._get_client()
            response = client.embeddings.create(
                model=self._model,
                input=text,
            )

            if not response.data or len(response.data) == 0:
                raise EmbeddingProviderResponseError(
                    "OpenAI returned empty embedding data"
                )

            embedding = response.data[0].embedding
            if not isinstance(embedding, list):
                raise EmbeddingProviderResponseError(
                    f"Expected embedding to be a list, got {type(embedding)}"
                )

            return list(embedding)

        except EmbeddingProviderNotConfiguredError:
            raise
        except EmbeddingProviderResponseError:
            raise
        except ImportError as e:
            raise EmbeddingProviderNotConfiguredError(str(e))
        except Exception as e:
            raise EmbeddingError(f"OpenAI API error: {e}")

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Batch embedding via OpenAI API.

        Parameters
        ----------
        texts:
            List of texts to embed.

        Returns
        -------
        list[list[float]]
            Embeddings in the same order as inputs.

        Raises
        ------
        EmbeddingError
            For API failures, timeouts, etc.
        EmbeddingProviderResponseError
            For invalid responses.
        """
        if not texts:
            return []

        try:
            client = self._get_client()
            response = client.embeddings.create(
                model=self._model,
                input=texts,
            )

            if not response.data or len(response.data) != len(texts):
                raise EmbeddingProviderResponseError(
                    f"Expected {len(texts)} embeddings, got {len(response.data)}"
                )

            # Sort by index to ensure order matches input
            sorted_data = sorted(response.data, key=lambda x: x.index)
            return [list(item.embedding) for item in sorted_data]

        except EmbeddingProviderNotConfiguredError:
            raise
        except EmbeddingProviderResponseError:
            raise
        except ImportError as e:
            raise EmbeddingProviderNotConfiguredError(str(e))
        except Exception as e:
            raise EmbeddingError(f"OpenAI API error: {e}")
