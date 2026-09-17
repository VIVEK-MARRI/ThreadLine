"""Stage 34 Part H: OpenAI provider contract tests + opt-in live tests.

Two layers:

OFFLINE (always run, network-free): stubbed SDK clients injected via the
providers' lazy ``_client`` slot exercise the real invocation shape,
response parsing, malformed-response handling, error propagation, and
configuration errors for all three providers.

LIVE (opt-in only): real SDK calls run ONLY when
RUN_LIVE_OPENAI_TESTS=true AND an API key is configured; otherwise they
skip cleanly.  Default CI never touches the network.  Live assertions are
structural only — no hard-coded model output text.
"""

import json
import os
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.extraction.openai_provider import (
    OpenAIExtractionProvider,
    _parse_extraction_payload,
)
from app.extraction.base import (
    ExtractionError,
    ExtractionProviderNotConfiguredError,
    ExtractionProviderResponseError,
)
from app.models.natural_language import EvidenceContext
from app.providers.base import (
    NLProviderError,
    NLProviderNotConfiguredError,
    NLProviderResponseError,
)
from app.providers.embedding_base import (
    EmbeddingError,
    EmbeddingProviderNotConfiguredError,
    EmbeddingProviderResponseError,
)
from app.providers.openai_embedding_provider import OpenAIEmbeddingProvider
from app.providers.openai_provider import (
    OpenAINaturalLanguageAnswerProvider,
    _parse_provider_response,
)

_LIVE = (
    os.getenv("RUN_LIVE_OPENAI_TESTS", "").strip().lower() == "true"
    and bool(settings.openai_api_key or os.getenv("OPENAI_API_KEY"))
)
needs_live = pytest.mark.skipif(
    not _LIVE, reason="live OpenAI tests require RUN_LIVE_OPENAI_TESTS=true and a key"
)


def _context():
    return EvidenceContext(
        context_text="EVIDENCE:\n[evabc123] gateway blocked",
        evidence_ids_in_context=["evabc123"],
    )


def _chat_client(captured, content=None, error=None):
    def create(**kwargs):
        captured.update(kwargs)
        if error is not None:
            raise error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )


def _embed_client(captured, data=None, error=None):
    def create(**kwargs):
        captured.update(kwargs)
        if error is not None:
            raise error
        return SimpleNamespace(data=data)

    return SimpleNamespace(embeddings=SimpleNamespace(create=create))


# ---------------------------------------------------------------------------
# NL answer provider: offline contract
# ---------------------------------------------------------------------------


class TestOpenAINLContract:
    def test_invocation_shape(self):
        captured = {}
        provider = OpenAINaturalLanguageAnswerProvider(model="test-model")
        provider._client = _chat_client(captured, "answer [evabc123]")
        answer = provider.generate_answer("what is blocked?", _context())
        assert captured["model"] == "test-model"
        assert captured["temperature"] == 0
        roles = [message["role"] for message in captured["messages"]]
        assert roles == ["system", "user"]
        assert "what is blocked?" in captured["messages"][1]["content"]
        assert answer.cited_evidence_ids == ["evabc123"]
        assert answer.insufficient_evidence is False

    def test_citations_deduplicated_in_order(self):
        parsed = _parse_provider_response("see [evabc123] and again [evabc123] plus [evdef456]")
        assert parsed.cited_evidence_ids == ["evabc123", "evdef456"]

    def test_insufficient_phrases_detected(self):
        parsed = _parse_provider_response("There is insufficient evidence to answer.")
        assert parsed.insufficient_evidence is True

    def test_empty_content_parses_without_crash(self):
        parsed = _parse_provider_response("")
        assert parsed.answer_text == ""
        assert parsed.cited_evidence_ids == []
        assert parsed.insufficient_evidence is False

    def test_sdk_error_propagates_as_provider_error(self):
        provider = OpenAINaturalLanguageAnswerProvider(model="m")
        provider._client = _chat_client({}, "x", error=RuntimeError("connection reset"))
        with pytest.raises(NLProviderError):
            provider.generate_answer("q?", _context())

    def test_missing_key_raises_not_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", None)
        provider = OpenAINaturalLanguageAnswerProvider(model="m")
        with pytest.raises(NLProviderNotConfiguredError):
            provider.generate_answer("q?", _context())


# ---------------------------------------------------------------------------
# Embedding provider: offline contract
# ---------------------------------------------------------------------------


class TestOpenAIEmbeddingContract:
    def test_embed_text_shape(self):
        captured = {}
        provider = OpenAIEmbeddingProvider(api_key="test-key", model="test-emb")
        provider._client = _embed_client(
            captured, [SimpleNamespace(embedding=[0.1, 0.2, 0.3])]
        )
        vector = provider.embed_text("hello")
        assert captured["model"] == "test-emb"
        assert captured["input"] == "hello"
        assert vector == [0.1, 0.2, 0.3]
        assert all(isinstance(value, float) for value in vector)

    def test_embed_texts_preserves_order_and_shape(self):
        provider = OpenAIEmbeddingProvider(api_key="test-key")
        provider._client = _embed_client(
            {},
            [
                SimpleNamespace(index=1, embedding=[2.0]),
                SimpleNamespace(index=0, embedding=[1.0]),
            ],
        )
        vectors = provider.embed_texts(["a", "b"])
        assert vectors == [[1.0], [2.0]]

    def test_empty_embedding_data_rejected(self):
        provider = OpenAIEmbeddingProvider(api_key="test-key")
        provider._client = _embed_client({}, [])
        with pytest.raises(EmbeddingProviderResponseError):
            provider.embed_text("hello")

    def test_batch_length_mismatch_rejected(self):
        provider = OpenAIEmbeddingProvider(api_key="test-key")
        provider._client = _embed_client(
            {}, [SimpleNamespace(index=0, embedding=[1.0])]
        )
        with pytest.raises(EmbeddingProviderResponseError):
            provider.embed_texts(["a", "b"])

    def test_non_list_embedding_rejected(self):
        provider = OpenAIEmbeddingProvider(api_key="test-key")
        provider._client = _embed_client({}, [SimpleNamespace(embedding="nope")])
        with pytest.raises(EmbeddingProviderResponseError):
            provider.embed_text("hello")

    def test_sdk_error_propagates_as_embedding_error(self):
        provider = OpenAIEmbeddingProvider(api_key="test-key")
        provider._client = _embed_client({}, error=RuntimeError("timeout"))
        with pytest.raises(EmbeddingError):
            provider.embed_text("hello")

    def test_missing_key_rejected_at_construction(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(EmbeddingProviderNotConfiguredError):
            OpenAIEmbeddingProvider(api_key=None)

    def test_empty_text_rejected(self):
        provider = OpenAIEmbeddingProvider(api_key="test-key")
        with pytest.raises(ValueError):
            provider.embed_text("")

    def test_empty_batch_returns_empty(self):
        provider = OpenAIEmbeddingProvider(api_key="test-key")
        assert provider.embed_texts([]) == []


# ---------------------------------------------------------------------------
# Extraction provider: offline contract
# ---------------------------------------------------------------------------

_VALID_PAYLOAD = {
    "issues": [
        {"description": "gateway blocked", "evidence": {"source_text": "gateway is blocked"}}
    ],
    "tasks": [
        {
            "description": "fix gateway",
            "owner": "Sam",
            "deadline": None,
            "evidence": {"source_text": "sam will fix it"},
        }
    ],
    "decisions": [],
    "risks": [
        {
            "description": "launch slips",
            "severity": "HIGH",
            "evidence": {"source_text": "launch may slip"},
        }
    ],
}


class TestOpenAIExtractionContract:
    def test_invocation_shape_json_mode(self):
        captured = {}
        provider = OpenAIExtractionProvider(model="test-model")
        provider._client = _chat_client(captured, json.dumps(_VALID_PAYLOAD))
        result = provider.extract("gateway is blocked", "m1")
        assert captured["model"] == "test-model"
        assert captured["response_format"] == {"type": "json_object"}
        assert captured["temperature"] == 0
        assert result.meeting_id == "m1"
        assert len(result.issues) == 1
        assert len(result.tasks) == 1
        assert len(result.risks) == 1
        assert result.issues[0].description == "gateway blocked"

    def test_parse_valid_payload(self):
        result = _parse_extraction_payload(_VALID_PAYLOAD, "m1")
        assert result.meeting_id == "m1"
        assert result.tasks[0].owner == "Sam"

    def test_non_json_content_rejected(self):
        provider = OpenAIExtractionProvider(model="m")
        provider._client = _chat_client({}, "not json {{{")
        with pytest.raises(ExtractionProviderResponseError):
            provider.extract("x", "m1")

    def test_schema_mismatch_rejected(self):
        bad = {"issues": [{"description": "no evidence key at all"}]}
        with pytest.raises(ExtractionProviderResponseError):
            _parse_extraction_payload(bad, "m1")

    def test_sdk_error_propagates_as_extraction_error(self):
        provider = OpenAIExtractionProvider(model="m")
        provider._client = _chat_client({}, "x", error=RuntimeError("rate limited"))
        with pytest.raises(ExtractionError):
            provider.extract("x", "m1")

    def test_missing_key_raises_not_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", None)
        provider = OpenAIExtractionProvider(model="m")
        with pytest.raises(ExtractionProviderNotConfiguredError):
            provider.extract("x", "m1")


# ---------------------------------------------------------------------------
# Live tests: real SDK calls, structural assertions only
# ---------------------------------------------------------------------------


class TestLiveOpenAI:
    def test_live_gate_reports_reason(self):
        if not _LIVE:
            pytest.skip("live OpenAI tests require RUN_LIVE_OPENAI_TESTS=true and a key")

    @needs_live
    def test_live_embedding_vector_shape(self):
        provider = OpenAIEmbeddingProvider()
        vector = provider.embed_text("ThreadLine live connectivity probe")
        assert isinstance(vector, list) and len(vector) > 100
        assert all(isinstance(value, float) for value in vector)

    @needs_live
    def test_live_extraction_result_shape(self):
        provider = OpenAIExtractionProvider()
        result = provider.extract(
            "The payment gateway is blocked. Sam will fix the billing helper tomorrow.",
            "live-probe",
        )
        assert result.meeting_id == "live-probe"
        assert isinstance(result.issues, list)

    @needs_live
    def test_live_answer_shape_with_citations(self):
        provider = OpenAINaturalLanguageAnswerProvider()
        answer = provider.generate_answer(
            "What is blocked?",
            EvidenceContext(
                context_text="EVIDENCE:\n[evabc123] The payment gateway is blocked.",
                evidence_ids_in_context=["evabc123"],
            ),
        )
        assert isinstance(answer.answer_text, str) and answer.answer_text.strip()
        assert isinstance(answer.cited_evidence_ids, list)
        assert isinstance(answer.insufficient_evidence, bool)
