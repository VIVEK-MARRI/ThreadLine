"""Stage 34 Part G: query routing fallback boundary regression tests.

Proves the classification → route/fallback boundary:
  * known intent → existing pipeline unchanged, fallback never consulted;
  * UNKNOWN + sufficient evidence → standard pipeline over probed items;
  * UNKNOWN + insufficient evidence → historical safe rejection verbatim;
  * no strategy → historical safe rejection verbatim;
  * probe gate unit semantics (attribution, overlap count, exceptions).
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.natural_language import (
    EntityResolutionStatus,
    EvidenceItem,
    EvidenceType,
    NaturalLanguageQuery,
    QueryIntent,
)
from app.services.evidence_context_builder import EvidenceContextBuilder
from app.services.natural_language_query_service import NaturalLanguageQueryService
from app.services.query_fallback import (
    LexicalSufficiencyFallback,
    question_tokens,
)
from app.services.query_intent_service import QueryIntentService

_NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)


def _item(evidence_id, summary, source_text=None, entity_id="e1", meeting_id="m1"):
    return EvidenceItem(
        evidence_id=evidence_id,
        evidence_type=EvidenceType.OBSERVATION,
        entity_id=entity_id,
        meeting_id=meeting_id,
        summary=summary,
        source_text=source_text,
    )


def _query(question):
    return NaturalLanguageQuery(
        query_id="q1", question=question, current_time=_NOW
    )


def _provider_answer(text="cited answer", cites=(), insufficient=False):
    return SimpleNamespace(
        answer_text=text,
        cited_evidence_ids=list(cites),
        warnings=[],
        insufficient_evidence=insufficient,
    )


def _service(provider_items=(), fallback=None, retrieved=()):
    provider = SimpleNamespace(
        generate_answer=lambda question, context: _provider_answer(
            cites=[i.evidence_id for i in provider_items or context.evidence_items]
        )
    )
    retrieval = SimpleNamespace(
        retrieve_evidence=lambda **kwargs: list(retrieved)
    )
    resolver = SimpleNamespace(
        resolve=lambda q: SimpleNamespace(
            status=EntityResolutionStatus.RESOLVED,
            entity_id="e1",
            extracted_name="X",
            candidate_names=[],
        ),
        resolve_by_id=lambda eid: SimpleNamespace(
            status=EntityResolutionStatus.RESOLVED,
            entity_id=eid,
            extracted_name="X",
            candidate_names=[],
        ),
    )
    return NaturalLanguageQueryService(
        intent_svc=QueryIntentService(),
        entity_resolver=resolver,
        retrieval_svc=retrieval,
        context_builder=EvidenceContextBuilder(),
        provider=provider,
        fallback_strategy=fallback,
    )


class _RecordingFallback:
    def __init__(self, items):
        self.calls = []
        self._items = items

    def probe(self, question, current_time, max_items):
        self.calls.append(question)
        return list(self._items)


class TestRoutingBoundary:
    def test_known_intent_never_consults_fallback(self):
        fallback = _RecordingFallback([_item("ev1", "anything here")])
        svc = _service(
            retrieved=[_item("ev1", "payment gateway blocked")],
            fallback=fallback,
        )
        answer = svc.query(_query("what is blocking the payment gateway"))
        assert answer.intent == QueryIntent.ENTITY_DEPENDENCIES
        assert fallback.calls == []
        assert answer.insufficient_evidence is False

    def test_unknown_with_sufficient_probe_runs_pipeline(self):
        items = [_item("ev1", "payment gateway blocked by billing helper")]
        svc = _service(provider_items=items, fallback=_RecordingFallback(items))
        answer = svc.query(_query("tell me about the payment gateway blockage"))
        assert answer.intent == QueryIntent.UNKNOWN
        assert answer.insufficient_evidence is False
        assert answer.evidence and answer.evidence[0].evidence_id == "ev1"
        assert answer.cited_evidence_ids == ["ev1"]
        assert any("unclear" in warning for warning in answer.warnings)

    def test_unknown_with_empty_probe_keeps_historical_rejection(self):
        svc = _service(fallback=_RecordingFallback([]))
        answer = svc.query(_query("should we fire alice"))
        assert answer.intent == QueryIntent.UNKNOWN
        assert answer.insufficient_evidence is True
        assert answer.answer == (
            "ThreadLine could not understand the intent of this question."
        )

    def test_unknown_without_strategy_keeps_historical_rejection(self):
        svc = _service()
        answer = svc.query(_query("should we fire alice"))
        assert answer.intent == QueryIntent.UNKNOWN
        assert answer.insufficient_evidence is True
        assert answer.answer == (
            "ThreadLine could not understand the intent of this question."
        )


class TestQuestionTokens:
    def test_short_tokens_excluded(self):
        assert question_tokens("should we fire alice") == {"should", "fire", "alice"}

    def test_empty_question_has_no_tokens(self):
        assert question_tokens("?!") == set()


class _StubSemantic:
    def __init__(self, matches=None, error=None):
        self._matches = matches or []
        self._error = error
        self.calls = []

    def search_persisted(self, query, source_lookup, top_k=5,
                         current_revision_lookup=None, organisation_id=None):
        self.calls.append(query)
        if self._error is not None:
            raise self._error
        return list(self._matches)


def _match(item):
    return SimpleNamespace(evidence=item, semantic_similarity_score=0.9)


class TestLexicalSufficiencyFallback:
    def _fallback(self, items=(), corpus=(), error=None):
        semantic = _StubSemantic(
            [_match(item) for item in items], error=error
        )
        return LexicalSufficiencyFallback(
            semantic_service=semantic,
            corpus_provider=lambda now: list(corpus),
        )

    def test_sufficient_overlap_returns_items(self):
        item = _item(
            "ev1",
            "payment gateway blocked",
            source_text="the payment gateway is blocked by the billing helper",
        )
        fallback = self._fallback(items=[item], corpus=[item])
        probed = fallback.probe("tell me about the payment gateway blockage", _NOW, 10)
        assert [i.evidence_id for i in probed] == ["ev1"]

    def test_empty_corpus_refuses(self):
        fallback = self._fallback(items=[], corpus=[])
        assert fallback.probe("tell me about the payment gateway blockage", _NOW, 10) == []

    def test_orphan_evidence_refuses(self):
        orphan = _item("ev9", "payment gateway blocked", entity_id=None, meeting_id=None)
        fallback = self._fallback(items=[orphan], corpus=[orphan])
        assert fallback.probe("tell me about the payment gateway blockage", _NOW, 10) == []

    def test_single_common_word_overlap_refuses(self):
        item = _item("ev1", "we should fix the build", source_text="we should fix the build")
        fallback = self._fallback(items=[item], corpus=[item])
        # Only "should" overlaps — one token is never enough.
        assert fallback.probe("should we fire alice", _NOW, 10) == []

    def test_no_overlap_refuses(self):
        item = _item("ev1", "payment gateway blocked")
        fallback = self._fallback(items=[item], corpus=[item])
        assert fallback.probe("quarterly hiring outlook", _NOW, 10) == []

    def test_probe_failure_refuses_without_raising(self):
        fallback = self._fallback(error=ValueError("embedding down"))
        assert fallback.probe("tell me about the payment gateway blockage", _NOW, 10) == []

    def test_empty_question_refuses(self):
        item = _item("ev1", "payment gateway blocked")
        fallback = self._fallback(items=[item], corpus=[item])
        assert fallback.probe("?!", _NOW, 10) == []
