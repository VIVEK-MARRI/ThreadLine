"""Natural Language Query API router (Stage 18).

Exposes the evidence-backed natural language intelligence layer.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.auth import Authorisation, get_request_context, require_permission
from app.api.changes import get_organisation_change_intelligence_service
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
from app.auth.models import Permission
from app.core.config import settings
from app.models.natural_language import NaturalLanguageQuery, make_query_id
from app.providers.base import (
    NLProviderError,
    NLProviderNotConfiguredError,
    NLProviderResponseError,
)
from app.providers.fake_embedding_provider import FakeEmbeddingProvider
from app.providers.openai_embedding_provider import OpenAIEmbeddingProvider
from app.repositories.semantic_index_repository import (
    AbstractSemanticIndexRepository,
    InMemorySemanticIndexRepository,
    JsonFileSemanticIndexRepository,
)
from app.schemas.query import (
    NaturalLanguageEvidenceResponse,
    NaturalLanguageQueryRequest,
    NaturalLanguageQueryResponse,
)
from app.services.evidence_context_builder import EvidenceContextBuilder
from app.services.evidence_retrieval_service import EvidenceRetrievalService
from app.services.hybrid_evidence_retrieval_service import (
    HybridEvidenceRetrievalService,
)
from app.services.natural_language_query_service import NaturalLanguageQueryService
from app.services.query_entity_resolver import QueryEntityResolver
from app.services.query_intent_service import QueryIntentService
from app.services.rate_limit import SlidingWindowRateLimiter
from app.services.semantic_evidence_retrieval_service import (
    SemanticEvidenceRetrievalService,
)
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
    current_revision_lookup=lambda meeting_id: (
        int(getattr(_meeting_repository.get_by_id(meeting_id), "source_revision", 1) or 1)
        if meeting_id is not None and _meeting_repository.get_by_id(meeting_id) is not None
        else None
    ),
)


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------

def get_evidence_retrieval_service(
    ctx: Authorisation = Depends(get_request_context),
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
    """FastAPI dependency that provides a tenant-scoped EvidenceRetrievalService."""
    return EvidenceRetrievalService(
        entity_repo=ctx.repos.entities,
        meeting_repo=ctx.repos.meetings,
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
    )


def get_natural_language_query_service(
    ctx: Authorisation = Depends(get_request_context),
    retrieval_svc: EvidenceRetrievalService = Depends(get_evidence_retrieval_service),
) -> NaturalLanguageQueryService:
    """FastAPI dependency that provides a tenant-scoped NaturalLanguageQueryService."""
    semantic_service = SemanticEvidenceRetrievalService(
        embedding_provider=_embedding_provider,
        repository=ctx.repos.semantic,
        embedding_model_name=settings.active_embedding_model,
        representation_version=settings.active_representation_version,
    )

    def _current_revision(meeting_id):
        if meeting_id is None:
            return None
        meeting = ctx.repos.meetings.get_by_id(meeting_id)
        if meeting is None:
            return None
        try:
            return int(getattr(meeting, "source_revision", 1) or 1)
        except (TypeError, ValueError):
            return None

    hybrid_service = HybridEvidenceRetrievalService(
        structured_service=retrieval_svc,
        semantic_service=semantic_service,
        semantic_corpus_provider=lambda current_time: retrieval_svc.build_semantic_corpus(current_time),
        current_revision_lookup=_current_revision,
        organisation_id=ctx.organisation_id,
    )
    # Stage 34 routing fallback: UNKNOWN questions probe the same bounded,
    # revision-guarded semantic path for overlapping citable evidence.
    from app.services.query_fallback import LexicalSufficiencyFallback

    fallback = LexicalSufficiencyFallback(
        semantic_service=semantic_service,
        corpus_provider=lambda current_time: retrieval_svc.build_semantic_corpus(current_time),
        current_revision_lookup=_current_revision,
        organisation_id=ctx.organisation_id,
    )
    return NaturalLanguageQueryService(
        intent_svc=_intent_service,
        entity_resolver=QueryEntityResolver(entity_repo=ctx.repos.entities),
        retrieval_svc=retrieval_svc,
        context_builder=_context_builder,
        provider=_nl_provider,
        hybrid_retrieval_svc=hybrid_service,
        fallback_strategy=fallback,
    )


# ---------------------------------------------------------------------------
# Expensive-endpoint rate limiting (Stage 34, Part F)
# ---------------------------------------------------------------------------
# POST /query (LLM + full retrieval) and POST /query/evidence (full
# retrieval) share one per-principal sliding window.  Keyed by stable
# authenticated user id; anonymous bootstrap callers fall back to client IP
# (bounded second dimension, same window).  Login's per-email throttle is
# untouched (separate mechanism in app.auth.service).
#
# The limiter is rebuilt when the configured limits change so tests can
# monkeypatch settings into isolation without cross-test leakage.

_query_limiter: SlidingWindowRateLimiter | None = None
_query_limiter_params: tuple | None = None


def _get_query_limiter() -> SlidingWindowRateLimiter:
    global _query_limiter, _query_limiter_params
    params = (
        settings.query_rate_limit_requests,
        settings.query_rate_limit_window_seconds,
    )
    if _query_limiter is None or _query_limiter_params != params:
        _query_limiter = SlidingWindowRateLimiter(
            max_requests=settings.query_rate_limit_requests,
            window_seconds=settings.query_rate_limit_window_seconds,
        )
        _query_limiter_params = params
    return _query_limiter


def _rate_limit_key(ctx: Authorisation, request: Request) -> str:
    if ctx.user is not None:
        return f"user:{ctx.user.user_id}"
    client = request.client.host if request.client is not None else "unknown"
    return f"ip:{client}"


def require_query_rate_limit(
    request: Request,
    ctx: Authorisation = Depends(get_request_context),
) -> None:
    """Deny over-quota expensive queries with 429 + Retry-After.

    Safe error shape only (no limiter internals, no identity material).
    """
    import math

    allowed, retry_after = _get_query_limiter().check(_rate_limit_key(ctx, request))
    if allowed:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "error": "rate_limited",
            "message": "Too many requests. Please slow down and try again.",
            "retry_after_seconds": math.ceil(retry_after),
        },
        headers={"Retry-After": str(math.ceil(retry_after))},
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
    _ctx: Authorisation = Depends(require_permission(Permission.QUERY_RUN)),
    _limited: None = Depends(require_query_rate_limit),
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
    _ctx: Authorisation = Depends(require_permission(Permission.QUERY_RUN)),
    _limited: None = Depends(require_query_rate_limit),
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
