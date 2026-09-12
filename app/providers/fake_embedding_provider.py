"""Fake / deterministic embedding provider for testing (Stage 19).

FakeEmbeddingProvider never makes network calls. It returns a deterministic
embedding vector derived purely from stable hashing of the input text.

Behaviour
---------
- Stable hash-based determinism: same input → same vector.
- Offline: no network, no API key, no external dependencies.
- Suitable for unit tests and deterministic architecture validation.
- Not suitable for semantic quality evaluation.

Vector generation
-----------------
1. Hash the input text using SHA-256.
2. Use the hex digest bytes as a seed for a deterministic pseudorandom sequence.
3. Generate EMBEDDING_DIMENSION floats in range [0.0, 1.0).
4. Normalize to unit length (cosine similarity compatibility).

Limitations
-----------
This embedding strategy is for deterministic testing only.
Real semantic embeddings from OpenAI or other services will provide
actual semantic understanding of text relationships.

Usage in tests
--------------
    from app.providers.fake_embedding_provider import FakeEmbeddingProvider

    provider = FakeEmbeddingProvider(dimension=256)
    vec_a = provider.embed_text("payment gateway")
    vec_b = provider.embed_text("payment gateway")
    assert vec_a == vec_b  # Deterministic

    vec_c = provider.embed_text("different text")
    # vec_c will be different from vec_a, but pseudo-randomly so
"""

import hashlib
from typing import Optional

from app.providers.embedding_base import AbstractEmbeddingProvider


# Default embedding dimension for fake provider
FAKE_EMBEDDING_DIMENSION: int = 256


class FakeEmbeddingProvider(AbstractEmbeddingProvider):
    """Deterministic hash-based embedding provider for testing.

    Produces stable, normalized embeddings suitable for cosine similarity
    calculations in unit tests. Does not provide semantic meaning.
    """

    def __init__(self, dimension: int = FAKE_EMBEDDING_DIMENSION) -> None:
        """
        Parameters
        ----------
        dimension:
            Length of the embedding vector. Default: 256.
        """
        if dimension <= 0:
            raise ValueError("dimension must be > 0")
        self._dimension = dimension

    def embed_text(self, text: str) -> list[float]:
        """Generate a deterministic embedding from text via stable hashing.

        Parameters
        ----------
        text:
            The text to embed. Must not be empty.

        Returns
        -------
        list[float]
            A normalized embedding vector of length dimension.
            All calls with the same text produce identical vectors.
        """
        if not text:
            raise ValueError("text must not be empty")

        # 1. Compute stable SHA-256 hash
        text_bytes = text.encode("utf-8")
        hash_digest = hashlib.sha256(text_bytes).digest()

        # 2. Use hash bytes as seed for deterministic pseudorandom generation
        #    We interpret the 32-byte SHA-256 digest as a sequence of values
        #    and repeat it as needed to fill the embedding dimension.
        embedding = []
        for i in range(self._dimension):
            # Cycle through hash bytes
            byte_index = i % len(hash_digest)
            byte_val = hash_digest[byte_index]

            # Scale byte (0-255) to (0.0-1.0) with offset per position
            # Add (i // 256) to vary across positions
            val = (byte_val + i) % 256
            normalized = val / 256.0
            embedding.append(normalized)

        # 3. Normalize to unit length for cosine similarity compatibility
        return self._normalize(embedding)

    def _normalize(self, vec: list[float]) -> list[float]:
        """Normalize vector to unit length (L2 norm).

        Parameters
        ----------
        vec:
            A list of floats.

        Returns
        -------
        list[float]
            The normalized vector (magnitude = 1.0 or 0.0 if input was zero).
        """
        magnitude_sq = sum(x * x for x in vec)

        if magnitude_sq < 1e-10:
            # Zero vector; return as-is
            return vec

        magnitude = magnitude_sq ** 0.5
        return [x / magnitude for x in vec]
