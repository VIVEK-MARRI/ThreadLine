"""Shared lexical normalisation and tokenisation utilities.

This module is the single source of truth for the lexical rules used across
the entity resolution pipeline.  Both the candidate generator and the
candidate scorer import from here so the tokenisation contract is
guaranteed to be identical in both stages.

Design notes
------------
- ``MIN_TOKEN_LENGTH`` controls which tokens participate in overlap scoring.
  Tokens shorter than this threshold are silently discarded.  This prevents
  single-letter fragments (e.g. ``"r"`` from the alias ``"R. Kumar"``) from
  producing spurious matches.
- ``_normalize`` is imported from the repository layer (the canonical
  normalisation contract shared by all resolution code).
- This module has no external dependencies beyond the standard library and
  ``app.repositories.entity_repository._normalize``.
"""

from app.repositories.entity_repository import _normalize

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_TOKEN_LENGTH: int = 2
"""Minimum token length (inclusive) to participate in overlap scoring.

Tokens shorter than this are silently discarded.  This prevents fragments
like ``"r"`` (from the alias ``"R. Kumar"``) from matching broadly.
Set to ``1`` to disable the guard.
"""


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------

def tokenize(text: str) -> frozenset[str]:
    """Normalise *text* and return a frozenset of meaningful tokens.

    Tokens shorter than :data:`MIN_TOKEN_LENGTH` characters are excluded.

    Parameters
    ----------
    text:
        Raw text to tokenise (e.g. a mention surface form or entity name).

    Returns
    -------
    frozenset[str]
        Set of lowercase, meaningful tokens.  Empty set if no token survives
        the length filter (e.g. input is a single-character string).
    """
    normalized = _normalize(text)
    return frozenset(
        token
        for token in normalized.split()
        if len(token) >= MIN_TOKEN_LENGTH
    )
