"""Unit tests for the keyword-based dependency extractor."""

from app.dependency_extraction.keyword_extractor import extract_relationship_statements
from app.models.relationships import RelationshipType


def test_extract_depends_on():
    """Verify various DEPENDS_ON patterns are extracted correctly."""
    source_text = "The Payment API depends on the Database Migration to finish."
    results = extract_relationship_statements(source_text)
    
    assert len(results) == 1
    assert "Payment API" in results[0].source_ref
    assert "Database Migration" in results[0].target_ref
    assert results[0].relationship_type == RelationshipType.DEPENDS_ON

    source_text_2 = "Frontend is waiting for backend deployment."
    results_2 = extract_relationship_statements(source_text_2)
    assert len(results_2) == 1
    assert "Frontend" in results_2[0].source_ref
    assert "backend deployment" in results_2[0].target_ref


def test_extract_blocks():
    """Verify various BLOCKS patterns are extracted correctly."""
    source_text = "The Database Migration is blocking the Payment API."
    results = extract_relationship_statements(source_text)
    
    assert len(results) == 1
    assert "Database Migration" in results[0].source_ref
    assert "Payment API" in results[0].target_ref
    assert results[0].relationship_type == RelationshipType.BLOCKS

    # Passive form: "Y is blocked by X" -> X BLOCKS Y
    source_text_2 = "The Payment API is blocked by the Database Migration."
    results_2 = extract_relationship_statements(source_text_2)
    assert any(
        "Database Migration" in r.source_ref and "Payment API" in r.target_ref 
        for r in results_2
    )
    assert all(r.relationship_type == RelationshipType.BLOCKS for r in results_2)


def test_extract_multiple():
    """Verify multiple statements in the same text are extracted."""
    source_text = "Auth depends on User Service. Also, Auth is blocking Analytics."
    results = extract_relationship_statements(source_text)
    
    assert len(results) == 2
    
    # We can't guarantee order if they match different patterns, so check presence
    deps = [r for r in results if r.relationship_type == RelationshipType.DEPENDS_ON]
    blocks = [r for r in results if r.relationship_type == RelationshipType.BLOCKS]
    
    assert len(deps) == 1
    # Check that Auth is extracted, it might grab leading words if not bounded well
    assert "Auth" in deps[0].source_ref
    assert "User Service" in deps[0].target_ref
    
    assert len(blocks) == 1
    assert "Auth" in blocks[0].source_ref
    assert "Analytics" in blocks[0].target_ref


def test_extract_deduplication():
    """Verify identical relationships are deduplicated."""
    # Matches "depends on" and "requires" for the same entities
    # To test dedup cleanly, don't include extra words that get swept into the source_ref
    source_text = "Auth depends on User Service. Auth requires User Service."
    results = extract_relationship_statements(source_text)
    
    assert len(results) == 1
    assert results[0].source_ref == "Auth"
    assert results[0].target_ref == "User Service"
    assert results[0].relationship_type == RelationshipType.DEPENDS_ON


def test_extract_no_match():
    """Verify unrelated text returns no statements."""
    source_text = "We discussed the weather and the upcoming launch."
    results = extract_relationship_statements(source_text)
    assert len(results) == 0


def test_extract_self_reference():
    """Verify self-references are discarded."""
    source_text = "Auth depends on auth."
    results = extract_relationship_statements(source_text)
    assert len(results) == 0
