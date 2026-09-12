"""Abstract base for embedding providers.

Any model that provides text embeddings for semantic evidence retrieval
must implement AbstractEmbeddingProvider. The semantic retrieval service
depends only on this interface — never on a concrete provider — so the
underlying model can be swapped without touching domain logic.

Provider contract
-----------------
- Receives a text string.
- Returns a list of floats representing the embedding vector.
- Must NOT make changes to repositories or entity state.
- May be nondeterministic (e.g., OpenAI with certain parameters).
  Fake provider MUST be fully deterministic.

To add a new provider:
1. Create a new module in app/providers/ (e.g., anthropic_embedding_provider.py).
2. Subclass AbstractEmbeddingProvider and implement embed_text().
3. Register it in app/core/config.py.
"""

from abc import ABC, abstractmethod


# ---------------------------------------------------------------------------
# Custom exception types
# ---------------------------------------------------------------------------

class EmbeddingError(Exception):
    """Raised when embedding generation fails."""


class EmbeddingProviderNotConfiguredError(EmbeddingError):
    """Raised when required credentials or configuration are missing.

    Surfaced as HTTP 503 so clients know to check server configuration
    rather than retry the same request.
    """


class EmbeddingProviderResponseError(EmbeddingError):
    """Raised when the provider response cannot be parsed or validated."""


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class AbstractEmbeddingProvider(ABC):
    """Text embedding provider interface.

    Implementors receive text and must return a deterministic vector.
    The provider is responsible for:

      - Calling the underlying model/API (if any).
      - Parsing the raw response into a list[float].
      - Raising EmbeddingError subclasses on failure.

    The provider must NOT:
      - Write to any repository or database.
      - Perform entity resolution or fact creation.
      - Infer or fabricate information not present in the input text.
    """

    @abstractmethod
    def embed_text(self, text: str) -> list[float]:
        """Embed a single text string.

        Parameters
        ----------
        text:
            The text to embed. Must not be empty.

        Returns
        -------
        list[float]
            A deterministic embedding vector.
            All calls with the same text must return the same vector.

        Raises
        ------
        EmbeddingProviderNotConfiguredError
            If the provider lacks required credentials or configuration.
        EmbeddingProviderResponseError
            If the provider response cannot be parsed or validated.
        EmbeddingError
            For any other provider-level failure (timeout, quota, etc.).
        """
        ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple text strings (optional batching optimization).

        Default implementation: call embed_text for each text.
        Subclasses may override for more efficient batched APIs.

        Parameters
        ----------
        texts:
            List of texts to embed.

        Returns
        -------
        list[list[float]]
            A list of embedding vectors, one per input text.
            Order must match input order.
        """
        return [self.embed_text(text) for text in texts]
