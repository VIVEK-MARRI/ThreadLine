"""Comprehensive unit tests for the keyword-based dependency extractor (Stage 15).

All tests are fully deterministic — no LLM calls, no network, no external database.

Coverage
--------
Pattern correctness (DEPENDS_ON):
  D01. "X depends on Y" — basic DEPENDS_ON.
  D02. "X is dependent on Y" — alternate DEPENDS_ON.
  D03. "X is waiting for Y" — DEPENDS_ON via waiting_for.
  D04. "X cannot proceed until Y" — DEPENDS_ON via cannot_proceed_until.
  D05. "X cannot proceed without Y" — DEPENDS_ON via cannot_proceed_without.
  D06. "X is blocked until Y" — DEPENDS_ON (dependency framing, not blocking entity).
  D07. "X requires Y" — DEPENDS_ON via requires.
  D08. "X needs Y" — DEPENDS_ON via needs.

Pattern correctness (BLOCKS):
  D09. "X is blocking Y" — active progressive BLOCKS.
  D10. "X blocks Y" — simple present BLOCKS.
  D11. "X blocked Y" — past tense active BLOCKS.
  D12. "Y is blocked by X" — passive → X BLOCKS Y (direction correct).
  D13. "Y is being blocked by X" — passive progressive → X BLOCKS Y (direction correct).

Participant normalization:
  D14. Leading "the" article is stripped from source_ref and target_ref.
  D15. Leading "a" article is stripped.
  D16. Leading "an" article is stripped.
  D17. Trailing "to finish" clause is stripped from target_ref.
  D18. Trailing "to proceed" clause is stripped from target_ref.
  D19. Trailing punctuation (period, comma) is stripped.
  D20. Article removal is case-insensitive ("The", "THE").

Directionality:
  D21. DEPENDS_ON: source is the dependent, target is the dependency.
  D22. BLOCKS active: source is the blocker, target is the blocked entity.
  D23. BLOCKS passive ("is blocked by"): X is blocker (source), Y is blocked (target).
  D24. BLOCKS passive progressive ("is being blocked by"): X is blocker, Y blocked.
  D25. "X is blocked until Y" does NOT produce BLOCKS (it produces DEPENDS_ON).

Deduplication and extraction guards:
  D26. Same logical relationship matched by two different patterns → 1 result.
  D27. Genuinely different relationships → both extracted.
  D28. Self-reference (X depends on X) → discarded.
  D29. Case-insensitive self-reference (X depends on x) → discarded.
  D30. No relationship language → empty list.

Edge cases:
  D31. "X blocked Y" past tense does NOT match "is blocked by" passive construction.
  D32. Multi-word entity names are preserved through normalization.
  D33. Text with both DEPENDS_ON and BLOCKS → both extracted correctly.
"""

import pytest

from app.dependency_extraction.keyword_extractor import (
    ExtractedRelationStatement,
    _normalize_participant,
    extract_relationship_statements,
)
from app.models.relationships import RelationshipType


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _find(results, rel_type=None, source_fragment=None, target_fragment=None):
    """Find matching result(s) from the list."""
    matches = []
    for r in results:
        if rel_type is not None and r.relationship_type != rel_type:
            continue
        if source_fragment is not None and source_fragment.lower() not in r.source_ref.lower():
            continue
        if target_fragment is not None and target_fragment.lower() not in r.target_ref.lower():
            continue
        matches.append(r)
    return matches


# ===========================================================================
# D01-D08: DEPENDS_ON pattern correctness
# ===========================================================================

def test_d01_depends_on_basic():
    """D01. 'X depends on Y' → DEPENDS_ON."""
    results = extract_relationship_statements("Payment API depends on Database Migration.")
    assert len(results) == 1
    r = results[0]
    assert r.relationship_type == RelationshipType.DEPENDS_ON
    assert "Payment API" in r.source_ref
    assert "Database Migration" in r.target_ref


def test_d02_is_dependent_on():
    """D02. 'X is dependent on Y' → DEPENDS_ON."""
    results = extract_relationship_statements("Frontend is dependent on Auth Service.")
    assert len(results) == 1
    r = results[0]
    assert r.relationship_type == RelationshipType.DEPENDS_ON
    assert "Frontend" in r.source_ref
    assert "Auth Service" in r.target_ref


def test_d03_is_waiting_for():
    """D03. 'X is waiting for Y' → DEPENDS_ON."""
    results = extract_relationship_statements("Billing is waiting for Payment Gateway.")
    assert len(results) == 1
    r = results[0]
    assert r.relationship_type == RelationshipType.DEPENDS_ON
    assert "Billing" in r.source_ref
    assert "Payment Gateway" in r.target_ref


def test_d04_cannot_proceed_until():
    """D04. 'X cannot proceed until Y' → DEPENDS_ON."""
    results = extract_relationship_statements("Deployment cannot proceed until Database Migration.")
    assert len(results) == 1
    r = results[0]
    assert r.relationship_type == RelationshipType.DEPENDS_ON
    assert "Deployment" in r.source_ref
    assert "Database Migration" in r.target_ref


def test_d05_cannot_proceed_without():
    """D05. 'X cannot proceed without Y' → DEPENDS_ON."""
    results = extract_relationship_statements("Release cannot proceed without Security Review.")
    assert len(results) == 1
    r = results[0]
    assert r.relationship_type == RelationshipType.DEPENDS_ON
    assert "Release" in r.source_ref
    assert "Security Review" in r.target_ref


def test_d06_is_blocked_until_produces_depends_on():
    """D06. 'X is blocked until Y' → DEPENDS_ON (not BLOCKS; Y is a dependency, not a blocker entity)."""
    results = extract_relationship_statements("Auth Service is blocked until Database Migration completes.")
    # Should produce at least one DEPENDS_ON
    deps = _find(results, rel_type=RelationshipType.DEPENDS_ON)
    assert len(deps) >= 1
    # Verify directionality
    d = deps[0]
    assert "Auth Service" in d.source_ref
    assert "Database Migration" in d.target_ref


def test_d07_requires():
    """D07. 'X requires Y' → DEPENDS_ON."""
    results = extract_relationship_statements("Frontend requires Auth Service.")
    deps = _find(results, rel_type=RelationshipType.DEPENDS_ON)
    assert len(deps) >= 1
    assert "Auth Service" in deps[0].target_ref


def test_d08_needs():
    """D08. 'X needs Y' → DEPENDS_ON."""
    results = extract_relationship_statements("Payment Service needs Database Migration.")
    deps = _find(results, rel_type=RelationshipType.DEPENDS_ON)
    assert len(deps) >= 1
    d = deps[0]
    assert "Payment Service" in d.source_ref
    assert "Database Migration" in d.target_ref


# ===========================================================================
# D09-D13: BLOCKS pattern correctness
# ===========================================================================

def test_d09_is_blocking():
    """D09. 'X is blocking Y' → BLOCKS."""
    results = extract_relationship_statements("Database Migration is blocking Payment API.")
    assert len(results) == 1
    r = results[0]
    assert r.relationship_type == RelationshipType.BLOCKS
    assert "Database Migration" in r.source_ref
    assert "Payment API" in r.target_ref


def test_d10_blocks_simple():
    """D10. 'X blocks Y' → BLOCKS."""
    results = extract_relationship_statements("Database Migration blocks Payment API.")
    assert len(results) == 1
    r = results[0]
    assert r.relationship_type == RelationshipType.BLOCKS
    assert "Database Migration" in r.source_ref
    assert "Payment API" in r.target_ref


def test_d11_blocked_past_tense():
    """D11. 'X blocked Y' past tense → BLOCKS."""
    results = extract_relationship_statements("Database Migration blocked Deployment.")
    blocks = _find(results, rel_type=RelationshipType.BLOCKS)
    assert len(blocks) >= 1
    b = blocks[0]
    assert "Database Migration" in b.source_ref
    assert "Deployment" in b.target_ref


def test_d12_passive_is_blocked_by_direction_correct():
    """D12. 'Y is blocked by X' → X BLOCKS Y (X=source/blocker, Y=target/blocked)."""
    results = extract_relationship_statements("Payment API is blocked by Database Migration.")
    blocks = _find(results, rel_type=RelationshipType.BLOCKS)
    assert len(blocks) >= 1
    b = blocks[0]
    # Blocker is source
    assert "Database Migration" in b.source_ref
    # Blocked is target
    assert "Payment API" in b.target_ref


def test_d13_passive_progressive_is_being_blocked_by_direction_correct():
    """D13. 'Y is being blocked by X' → X BLOCKS Y."""
    results = extract_relationship_statements("Frontend is being blocked by Auth Service.")
    blocks = _find(results, rel_type=RelationshipType.BLOCKS)
    assert len(blocks) >= 1
    b = blocks[0]
    assert "Auth Service" in b.source_ref
    assert "Frontend" in b.target_ref


# ===========================================================================
# D14-D20: Participant normalization
# ===========================================================================

def test_d14_strips_leading_the():
    """D14. Leading 'the' is stripped from both source and target."""
    results = extract_relationship_statements(
        "The Payment API depends on the Database Migration."
    )
    deps = _find(results, rel_type=RelationshipType.DEPENDS_ON)
    assert len(deps) == 1
    d = deps[0]
    # 'the' must NOT appear at the start of either participant
    assert not d.source_ref.lower().startswith("the ")
    assert not d.target_ref.lower().startswith("the ")
    assert "Payment API" in d.source_ref
    assert "Database Migration" in d.target_ref


def test_d15_strips_leading_a():
    """D15. Leading 'a' is stripped."""
    # Deliberately constructed sentence where 'a' is a grammatical article
    result = _normalize_participant("a Database Migration")
    assert result == "Database Migration"


def test_d16_strips_leading_an():
    """D16. Leading 'an' is stripped."""
    result = _normalize_participant("an Auth Service")
    assert result == "Auth Service"


def test_d17_strips_trailing_to_finish():
    """D17. Trailing 'to finish' clause is stripped."""
    result = _normalize_participant("Database Migration to finish")
    assert "to finish" not in result
    assert "Database Migration" in result


def test_d18_strips_trailing_to_proceed():
    """D18. Trailing 'to proceed' clause is stripped."""
    result = _normalize_participant("Database Migration to proceed")
    assert "to proceed" not in result


def test_d19_strips_trailing_punctuation():
    """D19. Trailing period and comma are stripped."""
    assert _normalize_participant("Database Migration.") == "Database Migration"
    assert _normalize_participant("Auth Service,") == "Auth Service"


def test_d20_article_removal_case_insensitive():
    """D20. Article removal is case-insensitive."""
    assert _normalize_participant("The Payment API") == "Payment API"
    assert _normalize_participant("THE payment api") == "payment api"
    assert _normalize_participant("An Auth Service") == "Auth Service"


# ===========================================================================
# D21-D25: Directionality
# ===========================================================================

def test_d21_depends_on_direction_source_is_dependent():
    """D21. DEPENDS_ON: source is the entity that depends, target is the dependency."""
    results = extract_relationship_statements("Frontend depends on Auth Service.")
    deps = _find(results, rel_type=RelationshipType.DEPENDS_ON)
    assert len(deps) == 1
    # Frontend (the dependent) is source
    assert "Frontend" in deps[0].source_ref
    # Auth Service (the dependency) is target
    assert "Auth Service" in deps[0].target_ref


def test_d22_blocks_active_direction_source_is_blocker():
    """D22. BLOCKS active: source is the blocker, target is the entity being blocked."""
    results = extract_relationship_statements("Database Migration is blocking Deployment.")
    blocks = _find(results, rel_type=RelationshipType.BLOCKS)
    assert len(blocks) == 1
    # Database Migration (the blocker) is source
    assert "Database Migration" in blocks[0].source_ref
    # Deployment (the blocked) is target
    assert "Deployment" in blocks[0].target_ref


def test_d23_blocks_passive_direction_correct():
    """D23. 'Y is blocked by X' → source=X (blocker), target=Y (blocked)."""
    results = extract_relationship_statements("Deployment is blocked by Database Migration.")
    blocks = _find(results, rel_type=RelationshipType.BLOCKS)
    assert len(blocks) >= 1
    b = blocks[0]
    # Database Migration is the blocker → source
    assert "Database Migration" in b.source_ref
    # Deployment is being blocked → target
    assert "Deployment" in b.target_ref


def test_d24_blocks_passive_progressive_direction_correct():
    """D24. 'Y is being blocked by X' → source=X (blocker), target=Y (blocked)."""
    results = extract_relationship_statements("Release is being blocked by Compliance Review.")
    blocks = _find(results, rel_type=RelationshipType.BLOCKS)
    assert len(blocks) >= 1
    b = blocks[0]
    assert "Compliance Review" in b.source_ref
    assert "Release" in b.target_ref


def test_d25_is_blocked_until_does_not_produce_blocks():
    """D25. 'X is blocked until Y' produces DEPENDS_ON, not BLOCKS."""
    results = extract_relationship_statements("Auth Service is blocked until Database Migration.")
    depends = _find(results, rel_type=RelationshipType.DEPENDS_ON)
    blocks_on_same = _find(
        results,
        rel_type=RelationshipType.BLOCKS,
        source_fragment="Auth Service",
        target_fragment="Database Migration",
    )
    # Must have DEPENDS_ON
    assert len(depends) >= 1
    # Must NOT have BLOCKS between the same participants
    assert len(blocks_on_same) == 0


# ===========================================================================
# D26-D30: Deduplication and guards
# ===========================================================================

def test_d26_deduplication_same_relationship_two_patterns():
    """D26. Same logical relationship matched by two patterns → 1 result."""
    # "Auth depends on User Service. Auth requires User Service."
    # Both "depends on" and "requires" produce (Auth, User Service, DEPENDS_ON)
    results = extract_relationship_statements(
        "Auth depends on User Service. Auth requires User Service."
    )
    deps = _find(results, rel_type=RelationshipType.DEPENDS_ON)
    # After deduplication, only one DEPENDS_ON for Auth→User Service
    auth_to_user = _find(
        deps,
        source_fragment="Auth",
        target_fragment="User Service",
    )
    assert len(auth_to_user) == 1


def test_d27_two_different_relationships_both_extracted():
    """D27. Two distinct relationships in the same text → both extracted."""
    results = extract_relationship_statements(
        "Auth depends on User Service. Auth is blocking Analytics."
    )
    deps = _find(results, rel_type=RelationshipType.DEPENDS_ON)
    blocks = _find(results, rel_type=RelationshipType.BLOCKS)
    assert len(deps) >= 1
    assert len(blocks) >= 1
    assert "User Service" in deps[0].target_ref
    assert "Analytics" in blocks[0].target_ref


def test_d28_self_reference_discarded():
    """D28. Self-reference (X depends on X) → discarded."""
    results = extract_relationship_statements("Auth depends on auth.")
    auth_self = _find(results, source_fragment="auth", target_fragment="auth")
    assert len(auth_self) == 0


def test_d29_case_insensitive_self_reference_discarded():
    """D29. Case-insensitive self-reference check catches 'Auth depends on AUTH'."""
    results = extract_relationship_statements("Frontend depends on FRONTEND.")
    self_refs = [r for r in results if r.source_ref.lower() == r.target_ref.lower()]
    assert len(self_refs) == 0


def test_d30_no_relationship_language():
    """D30. Text with no relationship language → empty list."""
    results = extract_relationship_statements(
        "We discussed the weather and the upcoming launch. The team was present."
    )
    assert len(results) == 0


# ===========================================================================
# D31-D33: Edge cases
# ===========================================================================

def test_d31_blocked_past_does_not_match_passive_is_blocked_by():
    """D31. 'X blocked Y' past tense must NOT match 'Y is blocked by X' passive.

    'Payment API is blocked by Database Migration' should produce:
      - BLOCKS: source=Database Migration, target=Payment API
    It must NOT produce any additional relationship where 'Payment API' is the
    source of BLOCKS via the 'blocked_past' pattern.
    """
    results = extract_relationship_statements(
        "Payment API is blocked by Database Migration."
    )
    blocks = _find(results, rel_type=RelationshipType.BLOCKS)
    # Should produce exactly the passive BLOCKS with correct direction
    assert len(blocks) == 1
    b = blocks[0]
    assert "Database Migration" in b.source_ref
    assert "Payment API" in b.target_ref
    # Verify that 'Payment API' is NOT mistakenly the source of any BLOCKS
    api_as_blocker = _find(
        results, rel_type=RelationshipType.BLOCKS, source_fragment="Payment API"
    )
    assert len(api_as_blocker) == 0


def test_d32_multi_word_entity_names_preserved():
    """D32. Multi-word entity names pass through normalization intact."""
    results = extract_relationship_statements(
        "Customer Support Service depends on Order Management System."
    )
    deps = _find(results, rel_type=RelationshipType.DEPENDS_ON)
    assert len(deps) == 1
    d = deps[0]
    assert d.source_ref == "Customer Support Service"
    assert d.target_ref == "Order Management System"


def test_d33_mixed_depends_on_and_blocks():
    """D33. Text with both DEPENDS_ON and BLOCKS → both extracted with correct types."""
    results = extract_relationship_statements(
        "Auth Service depends on Database Migration. "
        "Database Migration is blocking Deployment."
    )
    deps = _find(results, rel_type=RelationshipType.DEPENDS_ON)
    blocks = _find(results, rel_type=RelationshipType.BLOCKS)

    assert len(deps) == 1
    assert len(blocks) == 1

    assert "Auth Service" in deps[0].source_ref
    assert "Database Migration" in deps[0].target_ref

    assert "Database Migration" in blocks[0].source_ref
    assert "Deployment" in blocks[0].target_ref
