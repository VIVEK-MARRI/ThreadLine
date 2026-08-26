"""Extraction sub-package.

Contains the LLM provider abstraction and its implementations.
Import AbstractExtractionProvider from here to keep call sites clean.
"""

from app.extraction.base import AbstractExtractionProvider

__all__ = ["AbstractExtractionProvider"]
