"""Natural Language Query API router (Stage 18).

Exposes the evidence-backed natural language intelligence layer.
"""

import logging
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import settings
from app.api.entities import (
    _entity_repository,
    get_action_recommendation_service,
    get_attention_service,
    get_dependency_graph_service,
    get_entity_relationship_service,
    get_impact_analysis_service,
    get_insight_service,
    get_organisational_memory_service,
    get_portfolio_intelligence_service,
    get_unified_timeline_service,
)
from app.api.meetings import _meeting_repository
from app.api.changes import get_organisation_change_intelligence_service

from app.models.natural_language import NaturalLanguageQuery, make_query_id
from app.providers.base import (
    NLProviderError,
    NLProviderNotConfiguredError,
    NLProviderResponseError,
)
from app.schemas.query import (
    NaturalLanguageEvidenceResponse,
    NaturalLanguageQueryRequest,
    NaturalLanguageQueryResponse,
)
from app.services.evidence_context_builder import EvidenceContextBuilder
from app.services.evidence_retrieval_service import EvidenceRetrievalService
from app.services.natural_language_query_service import NaturalLanguageQueryService
from app.services.query_entity_resolver import QueryEntityResolver
from app.services.query_intent_service import QueryIntentService
from app.providers.fake_embedding_provider import FakeEmbeddingProvider
from app.providers.openai_embedding_provider import OpenAIEmbeddingProvider
from app.repositories.semantic_index_repository import (
    AbstractSemanticIndexRepository,
    InMemorySemanticIndexRepository,
    JsonFileSemanticIndexRepository,
)
from app.services.hybrid_evidence_retrieval_service import HybridEvidenceRetrievalService
from app.services.semantic_evidence_retrieval_service import SemanticEvidenceRetrievalService
from app.services.semantic_indexing_service import SemanticIndexingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/query", tags=["Natural Language"])


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def _build_nl_provider():
    """Instantiate the NL provider configured via NL_PROVIDER."""
    provider_name = settings.nl_provider.lower()

    if provider_name == "openai":
        from app.providers.openai_provider import OpenAINaturalLanguageAnswerProvider
        return OpenAINaturalLanguageAnswerProvider()

    if provider_name == "fake":
        from app.providers.fake_provider import FakeNaturalLanguageAnswerProvider
        return FakeNaturalLanguageAnswerProvider()

    raise ValueError(
        f"Unknown NL_PROVIDER value: '{settings.nl_provider}'.  "
        "Supported values: 'openai', 'fake'."
    )


_nl_provider = _build_nl_provider()
_intent_service = QueryIntentService()
_context_builder = EvidenceContextBuilder()
_entity_resolver = QueryEntityResolver(entity_repo=_entity_repository)


def _build_semantic_repository() -> AbstractSemanticIndexRepository:
    backend = settings.semantic_index_backend.lower()
    if backend == "in_memory":
        return InMemorySemanticIndexRepository()
    if backend == "persistent":
        return JsonFileSemanticIndexRepository(settings.semantic_index_path)
    raise ValueError(
        f"Unknown SEMANTIC_INDEX_BACKEND value: '{settings.semantic_index_backend}'. "
        "Supported values: 'in_memory', 'persistent'."
    )


def _build_embedding_provider():
    if settings.embedding_provider.lower() == "fake":
        return FakeEmbeddingProvider()
    if settings.embedding_provider.lower() == "openai":
        return OpenAIEmbeddingProvider(
            api_key=settings.openai_api_key,
            model=settings.active_embedding_model,
        )
    raise ValueError(f"Unknown EMBEDDING_PROVIDER value: '{settings.embedding_provider}'.")


_semantic_repository = _build_semantic_repository()
_embedding_provider = _build_embedding_provider()
_semantic_indexing_service = SemanticIndexingService(
    embedding_provider=_embedding_provider,
    repository=_semantic_repository,
    embedding_model_name=settings.active_embedding_model,
    representation_version=settings.active_representation_version,
)


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------

def get_evidence_retrieval_service(
    timeline_svc=Depends(get_unified_timeline_service),
    memory_svc=Depends(get_organisational_memory_service),
    insight_svc=Depends(get_insight_service),
    attention_svc=Depends(get_attention_service),
    action_svc=Depends(get_action_recommendation_service),
    dependency_graph_svc=Depends(get_dependency_graph_service),
    impact_svc=Depends(get_impact_analysis_service),
    org_change_svc=Depends(get_organisation_change_intelligence_service),
    portfolio_svc=Depends(get_portfolio_intelligence_service),
    relationship_svc=Depends(get_entity_relationship_service),
) -> EvidenceRetrievalService:
    """FastAPI dependency that provides a configured EvidenceRetrievalService."""
    return EvidenceRetrievalService(
        entity_repo=_entity_repository,
        meeting_repo=_meeting_repository,
        timeline_svc=timeline_svc,
        memory_svc=memory_svc,
        insight_svc=insight_svc,
        attention_svc=attention_svc,
        action_svc=action_svc,
        dependency_graph_svc=dependency_graph_svc,
        impact_svc=impact_svc,
        org_change_svc=org_change_svc,
        portfolio_svc=portfolio_svc,
        relationship_svc=relationship_svc,
        semantic_indexing_svc=_semantic_indexing_service,
    )


def get_natural_language_query_service(
    retrieval_svc: EvidenceRetrievalService = Depends(get_evidence_retrieval_service),
) -> NaturalLanguageQueryService:
    """FastAPI dependency that provides a configured NaturalLanguageQueryService."""
    semantic_service = SemanticEvidenceRetrievalService(
        embedding_provider=_embedding_provider,
        repository=_semantic_repository,
        embedding_model_name=settings.active_embedding_model,
        representation_version=settings.active_representation_version,
    )
    hybrid_service = HybridEvidenceRetrievalService(
        structured_service=retrieval_svc,
        semantic_service=semantic_service,
    )
    return NaturalLanguageQueryService(
        intent_svc=_intent_service,
        entity_resolver=_entity_resolver,
        retrieval_svc=retrieval_svc,
        context_builder=_context_builder,
        provider=_nl_provider,
        hybrid_retrieval_svc=hybrid_service,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=NaturalLanguageQueryResponse,
    status_code=status.HTTP_200_OK,
    summary="Ask ThreadLine a question",
    description=(
        "Ask a natural-language question. ThreadLine will deterministically "
        "retrieve structured evidence and use an LLM to generate a factual, "
        "evidence-backed answer with citations."
    ),
)
def submit_query(
    request: NaturalLanguageQueryRequest,
    service: NaturalLanguageQueryService = Depends(get_natural_language_query_service),
) -> NaturalLanguageQueryResponse:
    """Process a natural language query end-to-end."""
    now = datetime.now(timezone.utc)
    query_id = make_query_id(request.question, request.entity_id, now)
    
    query = NaturalLanguageQuery(
        query_id=query_id,
        question=request.question,
        entity_id=request.entity_id,
        current_time=now,
        max_evidence_items=request.max_evidence_items,
        include_source_text=request.include_source_text,
    )

    try:
        result = service.query(query)
    except NLProviderNotConfiguredError as exc:
        logger.error("NL provider not configured: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The natural language provider is not configured.  "
                "Check that OPENAI_API_KEY is set in your environment."
            ),
        ) from exc
    except NLProviderResponseError as exc:
        logger.error("NL provider returned invalid response: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "The natural language provider returned a response that could not be "
                "validated.  This is a temporary issue — please try again."
            ),
        ) from exc
    except NLProviderError as exc:
        logger.error("NL query failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The natural language service is temporarily unavailable.  "
                "Please try again later."
            ),
        ) from exc
        
    return NaturalLanguageQueryResponse(
        query_id=result.query_id,
        question=result.question,
        intent=result.intent,
        entity_id=result.entity_id,
        answer=result.answer,
        evidence=[e.model_dump() for e in result.evidence],
        cited_evidence_ids=result.cited_evidence_ids,
        insufficient_evidence=result.insufficient_evidence,
        warnings=result.warnings,
        generated_at=result.generated_at,
    )


@router.post(
    "/evidence",
    response_model=NaturalLanguageEvidenceResponse,
    status_code=status.HTTP_200_OK,
    summary="Preview evidence for a question",
    description=(
        "Returns the exact evidence items that would be passed to the LLM "
        "for the given question. No LLM calls are made. Useful for debugging "
        "and transparency."
    ),
)
def get_query_evidence(
    request: NaturalLanguageQueryRequest,
    service: NaturalLanguageQueryService = Depends(get_natural_language_query_service),
) -> NaturalLanguageEvidenceResponse:
    """Retrieve evidence without generating an answer."""
    now = datetime.now(timezone.utc)
    query_id = make_query_id(request.question, request.entity_id, now)
    
    query = NaturalLanguageQuery(
        query_id=query_id,
        question=request.question,
        entity_id=request.entity_id,
        current_time=now,
        max_evidence_items=request.max_evidence_items,
        include_source_text=request.include_source_text,
    )

    evidence_items = service.query_evidence_only(query)
    # Re-classify intent for response metadata
    intent = QueryIntentService().classify(request.question)
    
    return NaturalLanguageEvidenceResponse(
        query_id=query_id,
        question=request.question,
        intent=intent,
        entity_id=request.entity_id,
        evidence=[e.model_dump() for e in evidence_items],
    )
