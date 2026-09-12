from datetime import datetime, timezone

from app.models.natural_language import EvidenceItem, EvidenceType
from app.models.semantic_index import SemanticIndexRecord
from app.repositories.semantic_index_repository import InMemorySemanticIndexRepository
from app.services.semantic_evidence_retrieval_service import SemanticEvidenceRetrievalService
from app.services.semantic_indexing_service import SemanticIndexingService


class ControlledProvider:
    def embed_text(self, text):
        return [1.0, 0.0] if "payment" in text.lower() or "gateway" in text.lower() else [0.0, 1.0]


def evidence(eid, summary):
    return EvidenceItem(evidence_id=eid, evidence_type=EvidenceType.STATE, summary=summary, timestamp=datetime.now(timezone.utc))


def test_persisted_search_discovers_item_outside_structured_candidates():
    provider = ControlledProvider()
    repository = InMemorySemanticIndexRepository()
    item = evidence("e-payment", "Payments Gateway Migration is blocked on gateway approval.")
    SemanticIndexingService(provider, repository).index_evidence(item)
    service = SemanticEvidenceRetrievalService(provider, min_similarity=0.9, repository=repository)
    matches = service.search_persisted("What is holding up the payment rollout?", {item.evidence_id: item}.get)
    assert [match.evidence_id for match in matches] == ["e-payment"]
    assert matches[0].evidence is item


def test_unrelated_persisted_item_is_excluded():
    provider = ControlledProvider()
    repository = InMemorySemanticIndexRepository()
    payment = evidence("payment", "Payments Gateway approval")
    vacation = evidence("vacation", "Employee vacation policy")
    indexing = SemanticIndexingService(provider, repository)
    indexing.index_evidence(payment)
    indexing.index_evidence(vacation)
    service = SemanticEvidenceRetrievalService(provider, min_similarity=0.9, repository=repository)
    assert [m.evidence_id for m in service.search_persisted("payment rollout blocker", {"payment": payment, "vacation": vacation}.get)] == ["payment"]


def test_stale_persisted_record_is_excluded_without_write():
    provider = ControlledProvider()
    repository = InMemorySemanticIndexRepository()
    original = evidence("e1", "Payments Gateway approval")
    SemanticIndexingService(provider, repository).index_evidence(original)
    changed = evidence("e1", "Employee vacation policy")
    count = repository.count()
    service = SemanticEvidenceRetrievalService(provider, min_similarity=0.0, repository=repository)
    assert service.search_persisted("payment", {"e1": changed}.get) == []
    assert repository.count() == count


def test_active_model_and_version_are_isolated():
    repository = InMemorySemanticIndexRepository()
    now = datetime.now(timezone.utc)
    for model, version, vector in (("model-a", "1.0", [1.0, 0.0]), ("model-b", "2.0", [1.0, 0.0])):
        repository.upsert(SemanticIndexRecord(evidence_id=model, embedding=vector, embedding_model=model, embedding_dimension=2, representation_hash="x", representation_version=version, indexed_at=now))
    assert [record.evidence_id for record, _ in repository.search_similar([1.0, 0.0], "model-a", "1.0", 5, 0.0)] == ["model-a"]


def test_missing_source_is_discarded():
    repository = InMemorySemanticIndexRepository()
    item = evidence("e1", "Payments Gateway approval")
    SemanticIndexingService(ControlledProvider(), repository).index_evidence(item)
    service = SemanticEvidenceRetrievalService(ControlledProvider(), min_similarity=0.0, repository=repository)
    assert service.search_persisted("payment", lambda _: None) == []
