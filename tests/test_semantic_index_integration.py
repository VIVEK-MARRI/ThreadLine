"""Stage 21 persisted retrieval and source-safety tests."""

from datetime import datetime, timezone

from app.models.natural_language import EvidenceItem, EvidenceType, _make_evidence_id
from app.providers.fake_embedding_provider import FakeEmbeddingProvider
from app.repositories.semantic_index_repository import JsonFileSemanticIndexRepository
from app.services.semantic_evidence_retrieval_service import SemanticEvidenceRetrievalService
from app.services.semantic_indexing_service import SemanticIndexingService


def make_evidence(summary: str, entity_id: str = "e1") -> EvidenceItem:
    return EvidenceItem(
        evidence_id=_make_evidence_id(EvidenceType.STATE, entity_id, None, summary),
        evidence_type=EvidenceType.STATE,
        entity_id=entity_id,
        summary=summary,
        source_text="Payments Gateway Migration is blocked on gateway approval.",
        timestamp=datetime.now(timezone.utc),
        source_reference="Meeting m1, mention mn1",
    )


def make_services(tmp_path):
    repository = JsonFileSemanticIndexRepository(tmp_path / "index.json")
    provider = FakeEmbeddingProvider(dimension=64)
    indexing = SemanticIndexingService(provider, repository, "fake", "1.0")
    retrieval = SemanticEvidenceRetrievalService(
        provider, min_similarity=0.0, repository=repository,
        embedding_model_name="fake", representation_version="1.0",
    )
    return repository, indexing, retrieval, provider


def test_query_uses_persisted_embedding_after_service_recreation(tmp_path):
    evidence = make_evidence("Payments Gateway Migration is blocked")
    repository, indexing, _, _ = make_services(tmp_path)
    indexing.index_evidence(evidence)

    recreated_repository = JsonFileSemanticIndexRepository(tmp_path / "index.json")
    query_service = SemanticEvidenceRetrievalService(
        FakeEmbeddingProvider(dimension=64), min_similarity=0.0,
        repository=recreated_repository, embedding_model_name="fake",
        representation_version="1.0",
    )
    matches = query_service.search("gateway approval", [evidence])
    assert [match.evidence_id for match in matches] == [evidence.evidence_id]
    assert matches[0].evidence is evidence


def test_missing_record_is_excluded_without_embedding_generation(tmp_path):
    _, _, retrieval, provider = make_services(tmp_path)
    evidence = make_evidence("Missing index record")
    original = provider.embed_text
    calls = 0

    def counted(text):
        nonlocal calls
        calls += 1
        return original(text)

    provider.embed_text = counted
    assert retrieval.search("missing", [evidence]) == []
    assert calls == 1  # query only; the evidence is never re-embedded


def test_stale_record_is_excluded(tmp_path):
    _, indexing, retrieval, _ = make_services(tmp_path)
    original = make_evidence("Original representation")
    indexing.index_evidence(original)
    changed = make_evidence("Changed representation")
    assert retrieval.search("changed", [changed]) == []


def test_retry_is_idempotent_and_reuses_embedding(tmp_path):
    _, indexing, _, provider = make_services(tmp_path)
    evidence = make_evidence("Retryable evidence")
    calls = 0
    original = provider.embed_text

    def counted(text):
        nonlocal calls
        calls += 1
        return original(text)

    provider.embed_text = counted
    indexing.retry_indexing(evidence)
    indexing.retry_indexing(evidence)
    assert calls == 1


def test_consistency_reports_orphan_and_stale_records(tmp_path):
    _, indexing, _, _ = make_services(tmp_path)
    current = make_evidence("Current")
    orphan = make_evidence("Orphan", "e2")
    indexing.index_evidence(current)
    indexing.index_evidence(orphan)
    changed = make_evidence("Changed").model_copy(update={"evidence_id": current.evidence_id})
    report = indexing.check_consistency([changed])
    assert any("Missing source evidence" in issue for issue in report["issues"])
    assert any("Stale representation hash" in issue for issue in report["issues"])
