"""Dependency extraction module for Stage 15 — Explicit Dependency Intelligence.

This module provides the keyword-based relationship extractor that identifies
explicit dependency and blocking statements in meeting transcript excerpts.

The extractor is deterministic, requires no LLM, and operates only on the
``source_text`` field of already-resolved entity mentions.
"""
