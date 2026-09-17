"""Natural Language Query Orchestrator (Stage 18).

Coordinates the end-to-end evidence-backed answer generation pipeline.
This service brings together intent classification, entity resolution,
evidence retrieval, context building, and the provider.

Pipeline flow:
1. Intent Classification: Deterministically map question to QueryIntent.
2. Entity Resolution: If intent requires an entity, resolve name to ID.
3. Evidence Retrieval: Gather relevant evidence from domain services.
4. Context Building: Deduplicate, rank, bound, and format evidence.
5. Answer Generation: Call the abstract provider with the bounded context.
6. Citation Validation: Ensure provider only cites valid, supplied evidence IDs.
"""

import logging
from datetime import datetime, timezone

from app.models.natural_language import (
    EntityResolutionStatus,
    EvidenceItem,
    NaturalLanguageAnswer,
    NaturalLanguageQuery,
    QueryIntent,
)
from app.providers.base import AbstractNaturalLanguageAnswerProvider
from app.services.evidence_context_builder import EvidenceContextBuilder
from app.services.evidence_retrieval_service import EvidenceRetrievalService
from app.services.hybrid_evidence_retrieval_service import (
    HybridEvidenceRetrievalService,
)
from app.services.query_entity_resolver import QueryEntityResolver
from app.services.query_fallback import AbstractQueryFallbackStrategy
from app.services.query_intent_service import QueryIntentService

logger = logging.getLogger(__name__)


class NaturalLanguageQueryService:
    """Orchestrates the natural language query pipeline."""

    def __init__(
        self,
        intent_svc: QueryIntentService,
        entity_resolver: QueryEntityResolver,
        retrieval_svc: EvidenceRetrievalService,
        context_builder: EvidenceContextBuilder,
        provider: AbstractNaturalLanguageAnswerProvider,
        hybrid_retrieval_svc: HybridEvidenceRetrievalService | None = None,
        fallback_strategy: AbstractQueryFallbackStrategy | None = None,
    ) -> None:
        self._intent_svc = intent_svc
        self._entity_resolver = entity_resolver
        self._retrieval_svc = retrieval_svc
        self._context_builder = context_builder
        self._provider = provider
        self._hybrid_retrieval = hybrid_retrieval_svc
        # Stage 34 routing boundary: UNKNOWN intents consult this probe.
        # None preserves the historical immediate rejection exactly.
        self._fallback = fallback_strategy

    def _get_time(self, provided: datetime | None) -> datetime:
        return provided if provided else datetime.now(timezone.utc)

    def query(self, request: NaturalLanguageQuery) -> NaturalLanguageAnswer:
        """Process a natural language query end-to-end.

        Parameters
        ----------
        request: Validated query model.

        Returns
        -------
        NaturalLanguageAnswer: Grounded response.
        """
        current_time = self._get_time(request.current_time)
        warnings: list[str] = []
        
        # 1. Intent Classification
        intent = self._intent_svc.classify(request.question)
        logger.info("QueryService: classified intent=%s", intent.value)

        # Fast path for UNKNOWN: consult the fallback strategy boundary.
        # No strategy (or an empty probe) preserves the historical safe
        # rejection verbatim.  A non-empty probe falls through to the
        # standard evidence→answer pipeline below with intent UNKNOWN —
        # never a guessed answer, always cited bounded evidence.
        probed_items = None
        if intent == QueryIntent.UNKNOWN:
            if self._fallback is not None:
                probed_items = self._fallback.probe(
                    request.question, current_time, request.max_evidence_items
                )
                if probed_items:
                    warnings.append(
                        "Question intent was unclear; answering from the "
                        "evidence that lexically overlaps the question."
                    )
            if not probed_items:
                return NaturalLanguageAnswer(
                    query_id=request.query_id,
                    question=request.question,
                    intent=intent,
                    entity_id=request.entity_id,
                    answer="ThreadLine could not understand the intent of this question.",
                    insufficient_evidence=True,
                    warnings=warnings,
                    generated_at=datetime.now(timezone.utc),
                )

        # 2. Entity Resolution
        # Determine if intent requires an entity
        entity_intents = {
            QueryIntent.ENTITY_STATUS,
            QueryIntent.ENTITY_HISTORY,
            QueryIntent.ENTITY_RISKS,
            QueryIntent.ENTITY_DEPENDENCIES,
            QueryIntent.ENTITY_IMPACTS,
            QueryIntent.ENTITY_ACTIONS,
            QueryIntent.ENTITY_CHANGES,
        }
        
        resolved_entity_id = request.entity_id
        
        if intent in entity_intents:
            if resolved_entity_id:
                # Client provided ID explicitly, just verify it exists
                res = self._entity_resolver.resolve_by_id(resolved_entity_id)
            else:
                # Need to extract from question
                res = self._entity_resolver.resolve(request.question)
                
            if res.status == EntityResolutionStatus.UNRESOLVED:
                warnings.append(
                    f"Entity could not be resolved from question (extracted: {res.extracted_name})"
                )
                return NaturalLanguageAnswer(
                    query_id=request.query_id,
                    question=request.question,
                    intent=intent,
                    entity_id=None,
                    answer="ThreadLine could not identify the specific entity mentioned in your question.",
                    insufficient_evidence=True,
                    warnings=warnings,
                    generated_at=datetime.now(timezone.utc),
                )
            elif res.status == EntityResolutionStatus.AMBIGUOUS:
                warnings.append(
                    f"Entity reference is ambiguous. Plausible candidates: {', '.join(res.candidate_names)}"
                )
                return NaturalLanguageAnswer(
                    query_id=request.query_id,
                    question=request.question,
                    intent=intent,
                    entity_id=None,
                    answer=f"The entity name '{res.extracted_name}' is ambiguous. Please be more specific.",
                    insufficient_evidence=True,
                    warnings=warnings,
                    generated_at=datetime.now(timezone.utc),
                )
                
            resolved_entity_id = res.entity_id

        # 3. Evidence Retrieval (UNKNOWN fallback items bypass retrieval:
        # they already passed the sufficiency probe)
        if probed_items is not None:
            raw_items = probed_items
        elif self._hybrid_retrieval is not None:
            raw_items = self._hybrid_retrieval.retrieve_evidence(
                intent=intent,
                entity_id=resolved_entity_id,
                entity_resolution=res if intent in entity_intents else None,
                query_text=request.question,
                current_time=current_time,
                max_items=request.max_evidence_items,
            )
        else:
            raw_items = self._retrieval_svc.retrieve_evidence(
                intent=intent,
                entity_id=resolved_entity_id,
                current_time=current_time,
                max_items=request.max_evidence_items,
                include_source_text=request.include_source_text,
            )

        # 4. Context Building
        context = self._context_builder.build_context(
            raw_items=raw_items,
            max_items=request.max_evidence_items,
        )
        if context.was_truncated:
            warnings.append("Evidence was truncated due to length limits.")
            
        if not context.evidence_items:
            # Fast path for no evidence
            return NaturalLanguageAnswer(
                query_id=request.query_id,
                question=request.question,
                intent=intent,
                entity_id=resolved_entity_id,
                answer="ThreadLine does not have sufficient evidence to answer this question.",
                insufficient_evidence=True,
                warnings=warnings,
                generated_at=datetime.now(timezone.utc),
            )

        # 5. Answer Generation
        provider_answer = self._provider.generate_answer(
            question=request.question,
            context=context,
        )
        
        if provider_answer.warnings:
            warnings.extend(provider_answer.warnings)

        # 6. Citation Validation
        validated_cites: list[str] = []
        for cid in provider_answer.cited_evidence_ids:
            if cid in context.evidence_ids_in_context:
                if cid not in validated_cites:
                    validated_cites.append(cid)
            else:
                logger.warning("Provider cited invalid evidence ID: %s", cid)
                warnings.append(f"Provider cited invalid evidence ID: {cid}")

        return NaturalLanguageAnswer(
            query_id=request.query_id,
            question=request.question,
            intent=intent,
            entity_id=resolved_entity_id,
            answer=provider_answer.answer_text,
            evidence=context.evidence_items,
            cited_evidence_ids=validated_cites,
            insufficient_evidence=provider_answer.insufficient_evidence,
            warnings=warnings,
            generated_at=datetime.now(timezone.utc),
        )

    def query_evidence_only(self, request: NaturalLanguageQuery) -> list[EvidenceItem]:
        """Debug/Transparency API: return raw bounded evidence without calling the provider."""
        current_time = self._get_time(request.current_time)
        intent = self._intent_svc.classify(request.question)
        
        if intent == QueryIntent.UNKNOWN:
            return []
            
        entity_intents = {
            QueryIntent.ENTITY_STATUS,
            QueryIntent.ENTITY_HISTORY,
            QueryIntent.ENTITY_RISKS,
            QueryIntent.ENTITY_DEPENDENCIES,
            QueryIntent.ENTITY_IMPACTS,
            QueryIntent.ENTITY_ACTIONS,
            QueryIntent.ENTITY_CHANGES,
        }
        
        resolved_entity_id = request.entity_id
        if intent in entity_intents:
            if resolved_entity_id:
                res = self._entity_resolver.resolve_by_id(resolved_entity_id)
            else:
                res = self._entity_resolver.resolve(request.question)
            if res.status != EntityResolutionStatus.RESOLVED:
                return []
            resolved_entity_id = res.entity_id

        if self._hybrid_retrieval is not None:
            raw_items = self._hybrid_retrieval.retrieve_evidence(
                intent=intent,
                entity_id=resolved_entity_id,
                entity_resolution=res if intent in entity_intents else None,
                query_text=request.question,
                current_time=current_time,
                max_items=request.max_evidence_items,
            )
        else:
            raw_items = self._retrieval_svc.retrieve_evidence(
                intent=intent,
                entity_id=resolved_entity_id,
                current_time=current_time,
                max_items=request.max_evidence_items,
                include_source_text=request.include_source_text,
            )
        
        context = self._context_builder.build_context(
            raw_items=raw_items,
            max_items=request.max_evidence_items,
        )
        
        return context.evidence_items
