"""Entities API router.

Handles HTTP concerns only: routing, request parsing, response serialisation,
and HTTP error translation.  All business logic lives in EntityService and
CandidateService.

Route table
-----------
POST   /entities                                     Create or retrieve a canonical entity
GET    /entities                                     List entities (optional ?entity_type= filter)
GET    /entities/{entity_id}                         Retrieve a canonical entity by ID
POST   /entities/mentions                            Register an entity mention (resolved or unresolved)
GET    /entities/mentions/{mention_id}/candidates    Candidate generation for an unresolved mention
GET    /entities/{entity_id}/insights               Retrieve derived insights for an entity
GET    /entities/{entity_id}/attention              Retrieve prioritised attention for an entity

Ordering note
-------------
All /entities/mentions/* routes must be declared BEFORE /entities/{entity_id}
so that FastAPI routes the literal path segment "mentions" correctly rather
than treating it as a dynamic entity_id.

Sub-resource routes (/{entity_id}/timeline, /{entity_id}/correlations,
/{entity_id}/memory, /{entity_id}/insights, /{entity_id}/attention) must also be declared BEFORE
/{entity_id} to prevent FastAPI from incorrectly matching 'timeline',
'correlations', 'memory', 'insights', or 'attention' as entity_id values.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.models.entity import (
    CanonicalEntity,
    EntityMention,
    EntityType,
    ResolutionStatus,
)
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.entity_resolution.lexical_candidate_generator import LexicalCandidateGenerator
from app.schemas.entity import (
    CandidatesResponse,
    CreateEntityRequest,
    EntityCandidateSchema,
    EntityResponse,
    EntityTypeSchema,
    RegisterMentionRequest,
    RegisterMentionResponse,
    ResolutionDecisionResponse,
    ResolutionOutcomeSchema,
    ResolutionStatusSchema,
    ScoredCandidatesResponse,
    ScoredEntityCandidateSchema,
)
from app.schemas.correlation import EntityCorrelationResponse, EntityObservationSchema
from app.schemas.temporal import (
    EntityTimelineResponse,
    StateObservationSchema as TimelineObservationSchema,
    TemporalStateSchema,
)
from app.schemas.memory import (
    EntityMemoryResponse,
    EntityMemoryFactSchema,
    MemoryFactTypeSchema,
)
from app.schemas.insights import (
    EntityInsightsResponse,
    EntityInsightSchema,
    InsightTypeSchema,
    InsightSeveritySchema,
)
from app.schemas.attention import (
    EntityAttentionDetailResponse,
    EntityAttentionSchema,
)
from app.schemas.actions import (
    EntityActionsResponse,
    EntityActionSchema,
    ActionTypeSchema,
    ActionPrioritySchema,
)
from app.schemas.unified_timeline import (
    UnifiedEntityTimelineResponse,
    TimelineEventSchema,
    TimelineEventTypeSchema,
)
from app.services.candidate_service import CandidateService, MentionNotFoundError
from app.services.candidate_scoring_service import (
    CandidateScoringService,
    MentionNotFoundError as ScoringMentionNotFoundError,
)
from app.services.entity_service import EntityNotFoundError, EntityService
from app.services.resolution_service import (
    ResolutionService,
    MentionNotFoundError as ResolutionMentionNotFoundError,
)
from app.services.correlation_service import CorrelationService
from app.services.temporal_state_service import TemporalStateService
from app.services.organisational_memory_service import OrganisationalMemoryService
from app.services.insight_service import InsightService
from app.services.attention_service import AttentionService
from app.services.action_recommendation_service import ActionRecommendationService
from app.services.unified_timeline_service import UnifiedTimelineService
from app.temporal.state_interpreter import KeywordStateInterpreter
from app.temporal.transition_policy import DefaultTransitionPolicy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/entities", tags=["Entities"])

# ---------------------------------------------------------------------------
# Shared repository singletons
# (When we move to PostgreSQL we'll replace these with session-scoped factories.)
# ---------------------------------------------------------------------------
_entity_repository = InMemoryEntityRepository()
_mention_repository = InMemoryMentionRepository()

# Shared meeting repository — imported from the meetings router so that
# correlation queries see meetings ingested via POST /meetings.
# This import is deferred to avoid circular-import issues at module load time;
# get_meeting_repository() is called at request time inside the dependency.
from app.api.meetings import get_meeting_repository as _get_shared_meeting_repository  # noqa: E402


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------

def get_entity_service() -> EntityService:
    """FastAPI dependency that provides a configured EntityService."""
    return EntityService(
        entity_repo=_entity_repository,
        mention_repo=_mention_repository,
    )


def get_candidate_service() -> CandidateService:
    """FastAPI dependency that provides a configured CandidateService.

    Uses the same shared repository singletons as get_entity_service so
    both services see the same in-memory state.  The LexicalCandidateGenerator
    is stateless and can be shared or recreated freely.
    """
    return CandidateService(
        mention_repo=_mention_repository,
        entity_repo=_entity_repository,
        generator=LexicalCandidateGenerator(),
    )


def get_candidate_scoring_service() -> CandidateScoringService:
    """FastAPI dependency that provides a configured CandidateScoringService.

    Uses the same shared repository singletons so all three services see
    the same in-memory state.  Both the generator and scorer are stateless
    and can be recreated freely.
    """
    from app.entity_resolution.lexical_candidate_scorer import LexicalCandidateScorer

    return CandidateScoringService(
        mention_repo=_mention_repository,
        entity_repo=_entity_repository,
        generator=LexicalCandidateGenerator(),
        scorer=LexicalCandidateScorer(),
    )


def get_resolution_service() -> ResolutionService:
    """FastAPI dependency that provides a configured ResolutionService.

    Uses the same shared repository singletons and a freshly constructed
    CandidateScoringService + ThresholdResolutionPolicy.  All components
    are stateless and can be recreated freely.
    """
    from app.entity_resolution.lexical_candidate_scorer import LexicalCandidateScorer
    from app.entity_resolution.resolution_policy import ThresholdResolutionPolicy

    scoring_service = CandidateScoringService(
        mention_repo=_mention_repository,
        entity_repo=_entity_repository,
        generator=LexicalCandidateGenerator(),
        scorer=LexicalCandidateScorer(),
    )
    return ResolutionService(
        mention_repo=_mention_repository,
        entity_repo=_entity_repository,
        scoring_service=scoring_service,
        policy=ThresholdResolutionPolicy(),
    )


def get_temporal_state_service() -> TemporalStateService:
    """FastAPI dependency that provides a configured TemporalStateService.

    Uses the shared entity, mention, and meeting repository singletons plus
    the default keyword-based interpreter and transition policy.
    All components are stateless and can be recreated freely.
    """
    return TemporalStateService(
        entity_repo=_entity_repository,
        mention_repo=_mention_repository,
        meeting_repo=_get_shared_meeting_repository(),
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
    )


def get_correlation_service() -> CorrelationService:
    """FastAPI dependency that provides a configured CorrelationService.

    Uses the shared entity and mention repository singletons plus the
    meeting repository singleton from the meetings router — all three must
    refer to the same in-memory stores for correlation to see data created
    by other endpoints.
    """
    return CorrelationService(
        entity_repo=_entity_repository,
        mention_repo=_mention_repository,
        meeting_repo=_get_shared_meeting_repository(),
    )


def get_organisational_memory_service() -> OrganisationalMemoryService:
    """FastAPI dependency that provides a configured OrganisationalMemoryService.

    Uses the shared entity, mention, and meeting repository singletons plus
    the default keyword-based interpreter and transition policy.
    OrganisationalMemoryService internally composes TemporalStateService,
    which in turn composes CorrelationService — all share the same stores.
    All components are stateless and can be recreated freely.
    """
    return OrganisationalMemoryService(
        entity_repo=_entity_repository,
        mention_repo=_mention_repository,
        meeting_repo=_get_shared_meeting_repository(),
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
    )


def get_insight_service() -> InsightService:
    """FastAPI dependency that provides a configured InsightService.

    Uses the shared entity, mention, and meeting repository singletons plus
    the default keyword-based interpreter and transition policy.
    InsightService internally composes OrganisationalMemoryService and
    TemporalStateService — all share the same stores.
    All components are stateless and can be recreated freely.
    """
    return InsightService(
        entity_repo=_entity_repository,
        mention_repo=_mention_repository,
        meeting_repo=_get_shared_meeting_repository(),
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
    )


def get_attention_service() -> AttentionService:
    """FastAPI dependency that provides a configured AttentionService.

    Uses the shared entity, mention, and meeting repository singletons plus
    the default keyword-based interpreter and transition policy.
    AttentionService internally composes InsightService.
    All components are stateless and can be recreated freely.
    """
    return AttentionService(
        entity_repo=_entity_repository,
        mention_repo=_mention_repository,
        meeting_repo=_get_shared_meeting_repository(),
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
    )


def get_action_recommendation_service() -> ActionRecommendationService:
    """FastAPI dependency that provides a configured ActionRecommendationService.

    Uses the shared repository singletons and interpreter/policy.
    """
    return ActionRecommendationService(
        entity_repo=_entity_repository,
        mention_repo=_mention_repository,
        meeting_repo=_get_shared_meeting_repository(),
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
    )


def get_unified_timeline_service() -> UnifiedTimelineService:
    """FastAPI dependency that provides a configured UnifiedTimelineService."""
    return UnifiedTimelineService(
        entity_repo=_entity_repository,
        mention_repo=_mention_repository,
        meeting_repo=_get_shared_meeting_repository(),
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
    )


# ---------------------------------------------------------------------------
# Translation helpers
# (Domain model → API response schema — keeps endpoint handlers thin.)
# ---------------------------------------------------------------------------

def _entity_to_response(entity: CanonicalEntity) -> EntityResponse:
    """Translate a CanonicalEntity domain model to the API response schema."""
    return EntityResponse(
        entity_id=entity.entity_id,
        entity_type=EntityTypeSchema(entity.entity_type.value),
        canonical_name=entity.canonical_name,
        aliases=entity.aliases,
        created_at=entity.created_at,
    )


def _mention_to_response(mention: EntityMention) -> RegisterMentionResponse:
    """Translate an EntityMention domain model to the API response schema."""
    return RegisterMentionResponse(
        mention_id=mention.mention_id,
        text=mention.text,
        entity_type=EntityTypeSchema(mention.entity_type.value),
        entity_id=mention.entity_id,
        resolution_status=ResolutionStatusSchema(mention.resolution_status.value),
        created_at=mention.created_at,
    )


def _candidates_to_response(
    mention: EntityMention,
    candidates: list,
) -> CandidatesResponse:
    """Translate (EntityMention, list[EntityCandidate]) to CandidatesResponse."""
    from app.models.entity import EntityCandidate  # local import avoids circular at module level

    candidate_schemas = [
        EntityCandidateSchema(
            entity_id=c.entity_id,
            entity_type=EntityTypeSchema(c.entity_type.value),
            canonical_name=c.canonical_name,
            candidate_reason=c.candidate_reason,
        )
        for c in candidates
    ]
    return CandidatesResponse(
        mention_id=mention.mention_id,
        resolution_status=ResolutionStatusSchema(mention.resolution_status.value),
        candidates=candidate_schemas,
    )


def _scored_candidates_to_response(
    mention: EntityMention,
    scored_candidates: list,
) -> ScoredCandidatesResponse:
    """Translate (EntityMention, list[ScoredEntityCandidate]) to ScoredCandidatesResponse."""
    from app.models.entity import ScoredEntityCandidate  # local import avoids circular at module level

    scored_schemas = [
        ScoredEntityCandidateSchema(
            entity_id=sc.entity_id,
            canonical_name=sc.canonical_name,
            score=sc.score,
            scoring_method=sc.scoring_method,
            matched_representation=sc.matched_representation,
            mention_coverage=sc.mention_coverage,
            candidate_coverage=sc.candidate_coverage,
            exact_match=sc.exact_match,
        )
        for sc in scored_candidates
    ]
    return ScoredCandidatesResponse(
        mention_id=mention.mention_id,
        resolution_status=ResolutionStatusSchema(mention.resolution_status.value),
        candidates=scored_schemas,
    )


def _decision_to_response(decision) -> ResolutionDecisionResponse:
    """Translate a ResolutionDecision domain model to the API response schema."""
    return ResolutionDecisionResponse(
        mention_id=decision.mention_id,
        outcome=ResolutionOutcomeSchema(decision.outcome.value),
        selected_entity_id=decision.selected_entity_id,
        top_score=decision.top_score,
        second_score=decision.second_score,
        score_margin=decision.score_margin,
        reason=decision.reason,
    )


def _insights_to_response(
    entity_id: str,
    insights: list,
) -> EntityInsightsResponse:
    """Translate (entity_id, list[EntityInsight]) to EntityInsightsResponse."""
    insight_schemas = [
        EntityInsightSchema(
            insight_id=i.insight_id,
            entity_id=i.entity_id,
            insight_type=InsightTypeSchema(i.insight_type.value),
            title=i.title,
            description=i.description,
            severity=InsightSeveritySchema(i.severity.value),
            observed_at=i.observed_at,
            related_meeting_id=i.related_meeting_id,
            evidence=i.evidence,
            deterministic_sort_key=i.deterministic_sort_key,
        )
        for i in insights
    ]
    return EntityInsightsResponse(
        entity_id=entity_id,
        insight_count=len(insight_schemas),
        insights=insight_schemas,
    )


def _attention_to_entity_response(
    entity_id: str,
    attention,
) -> EntityAttentionDetailResponse:
    """Translate (entity_id, EntityAttention) to EntityAttentionDetailResponse."""
    if attention is None:
        return EntityAttentionDetailResponse(
            entity_id=entity_id,
            has_attention=False,
            attention=None,
        )

    schema = EntityAttentionSchema(
        attention_id=attention.attention_id,
        entity_id=attention.entity_id,
        attention_level=attention.attention_level.value,  # type: ignore[arg-type]
        score=attention.score,
        reasons=[r.value for r in attention.reasons],  # type: ignore[misc]
        related_insight_ids=attention.related_insight_ids,
        evaluated_at=attention.evaluated_at,
    )
    return EntityAttentionDetailResponse(
        entity_id=entity_id,
        has_attention=True,
        attention=schema,
    )


def _actions_to_response(
    entity_id: str,
    actions: list,
) -> EntityActionsResponse:
    """Translate (entity_id, list[EntityAction]) to EntityActionsResponse."""
    action_schemas = [
        EntityActionSchema(
            action_id=a.action_id,
            entity_id=a.entity_id,
            action_type=ActionTypeSchema(a.action_type.value),
            priority=ActionPrioritySchema(a.priority.value),
            recommended_action=a.recommended_action,
            reason=a.reason,
            related_insight_ids=a.related_insight_ids,
            related_meeting_id=a.related_meeting_id,
            created_from_observation_at=a.created_from_observation_at,
            deterministic_sort_key=a.deterministic_sort_key,
        )
        for a in actions
    ]
    return EntityActionsResponse(
        entity_id=entity_id,
        action_count=len(action_schemas),
        actions=action_schemas,
    )


def _unified_timeline_to_response(
    timeline
) -> UnifiedEntityTimelineResponse:
    """Translate a UnifiedEntityTimeline domain model to API response schema."""
    event_schemas = [
        TimelineEventSchema(
            event_id=e.event_id,
            entity_id=e.entity_id,
            event_type=TimelineEventTypeSchema(e.event_type.value),
            occurred_at=e.occurred_at,
            related_meeting_id=e.related_meeting_id,
            title=e.title,
            description=e.description,
            event_metadata=e.event_metadata,
        )
        for e in timeline.events
    ]
    return UnifiedEntityTimelineResponse(
        entity_id=timeline.entity_id,
        first_observed_at=timeline.first_observed_at,
        last_observed_at=timeline.last_observed_at,
        event_count=len(event_schemas),
        events=event_schemas,
    )


def _memory_to_response(memory) -> EntityMemoryResponse:
    """Translate an EntityMemory domain model to the API response schema."""
    fact_schemas = [
        EntityMemoryFactSchema(
            fact_type=MemoryFactTypeSchema(f.fact_type.value),
            value=f.value,
            source_meeting_id=f.source_meeting_id,
            source_mention_id=f.source_mention_id,
            observed_at=f.observed_at,
            detail=f.detail,
        )
        for f in memory.facts
    ]
    return EntityMemoryResponse(
        entity_id=memory.entity_id,
        canonical_name=memory.canonical_name,
        entity_type=EntityTypeSchema(memory.entity_type.value),
        first_observed_at=memory.first_observed_at,
        last_observed_at=memory.last_observed_at,
        meeting_count=memory.meeting_count,
        observation_count=memory.observation_count,
        current_state=TemporalStateSchema(memory.current_state.value),
        facts=fact_schemas,
    )


def _timeline_to_response(timeline) -> EntityTimelineResponse:
    """Translate an EntityTimeline domain model to the API response schema."""
    obs_schemas = [
        TimelineObservationSchema(
            observation_index=obs.observation_index,
            meeting_id=obs.meeting_id,
            meeting_title=obs.meeting_title,
            meeting_date=obs.meeting_date,
            mention_id=obs.mention_id,
            evidence_text=obs.evidence_text,
            interpreted_state=TemporalStateSchema(obs.interpreted_state.value),
            transition_occurred=obs.transition_occurred,
            from_state=TemporalStateSchema(obs.from_state.value),
            to_state=TemporalStateSchema(obs.to_state.value),
            is_valid_transition=obs.is_valid_transition,
            transition_skipped_reason=obs.transition_skipped_reason,
        )
        for obs in timeline.timeline
    ]
    return EntityTimelineResponse(
        entity_id=timeline.entity_id,
        canonical_name=timeline.canonical_name,
        entity_type=EntityTypeSchema(timeline.entity_type.value),
        current_state=TemporalStateSchema(timeline.current_state.value),
        observation_count=timeline.observation_count,
        transition_count=timeline.transition_count,
        timeline=obs_schemas,
    )


def _correlation_to_response(correlation) -> EntityCorrelationResponse:
    """Translate an EntityCorrelation domain model to the API response schema."""
    observation_schemas = [
        EntityObservationSchema(
            meeting_id=obs.meeting_id,
            meeting_title=obs.meeting_title,
            meeting_date=obs.meeting_date,
            mention_id=obs.mention_id,
            mention_text=obs.mention_text,
            source_text=obs.source_text,
        )
        for obs in correlation.observations
    ]
    return EntityCorrelationResponse(
        entity_id=correlation.entity_id,
        canonical_name=correlation.canonical_name,
        entity_type=EntityTypeSchema(correlation.entity_type.value),
        observation_count=len(observation_schemas),
        observations=observation_schemas,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/mentions",
    response_model=RegisterMentionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register an entity mention",
    description=(
        "Register an observed reference to an entity from a meeting transcript.  "
        "The system attempts an exact-match (case-insensitive, whitespace-normalised) "
        "resolution against the entity registry.  "
        "If a match is found the mention is RESOLVED to that entity.  "
        "If no match is found the mention is stored as UNRESOLVED — "
        "a new canonical entity is NOT automatically created."
    ),
)
def register_mention(
    request: RegisterMentionRequest,
    service: EntityService = Depends(get_entity_service),
) -> RegisterMentionResponse:
    """Register a mention and return its resolution status."""
    mention = service.register_mention(
        entity_type=EntityType(request.entity_type.value),
        text=request.text,
        meeting_id=request.meeting_id,
        source_text=request.source_text,
    )
    return _mention_to_response(mention)


@router.post(
    "",
    response_model=EntityResponse,
    summary="Create a canonical entity",
    description=(
        "Create a new canonical entity in the registry.  "
        "If an entity of the same type with the same canonical name (after "
        "case-insensitive, whitespace-normalised comparison) already exists, "
        "that entity is returned with HTTP 200 instead of 201.  "
        "This prevents accidental duplicates without requiring a separate lookup."
    ),
)
def create_entity(
    request: CreateEntityRequest,
    service: EntityService = Depends(get_entity_service),
) -> EntityResponse:
    """Create (or retrieve) a canonical entity and return it."""
    entity, created = service.create_entity(
        entity_type=EntityType(request.entity_type.value),
        canonical_name=request.canonical_name,
    )
    # Use 201 for genuinely new entities, 200 when returning an existing one.
    # FastAPI sets the default status_code on the decorator; we override here
    # for the existing-entity case by returning a Response directly is one option,
    # but for simplicity and client ergonomics we always return 200 on this endpoint.
    # The body is identical in both cases — clients that care about creation
    # vs. retrieval can compare entity_id with a previously known value.
    # We annotate the distinction in the log only.
    if created:
        logger.info("API: created new entity %s", entity.entity_id)
    else:
        logger.info("API: returning existing entity %s", entity.entity_id)
    return _entity_to_response(entity)


@router.get(
    "/mentions/{mention_id}/candidates",
    response_model=CandidatesResponse,
    summary="Generate candidates for an unresolved mention",
    description=(
        "Return an ordered list of canonical entity candidates for an unresolved mention.\n\n"
        "Candidate generation is NOT final entity resolution — it is a high-recall "
        "triage step that surfaces entities worth evaluating in a future scoring stage.\n\n"
        "**If the mention is already RESOLVED**, the response is HTTP 200 with an empty "
        "candidates list (the mention already has a confirmed identity).\n\n"
        "**Ordering**: candidates are sorted by descending token-overlap count, then "
        "alphabetical canonical_name, then entity_id as a stable tie-breaker.\n\n"
        "This endpoint is read-only: it never modifies the mention's resolution_status "
        "or entity_id."
    ),
)
def get_mention_candidates(
    mention_id: str,
    service: CandidateService = Depends(get_candidate_service),
) -> CandidatesResponse:
    """Return candidate entities for the given mention."""
    try:
        mention, candidates = service.get_candidates(mention_id)
    except MentionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return _candidates_to_response(mention, candidates)


@router.get(
    "/mentions/{mention_id}/scored-candidates",
    response_model=ScoredCandidatesResponse,
    summary="Score candidates for an unresolved mention",
    description=(
        "Return a scored, ranked list of canonical entity candidates for an "
        "unresolved mention.\n\n"
        "Candidate scoring evaluates **how strong the lexical evidence is** "
        "for each candidate — it is NOT a resolution decision and never assigns "
        "an entity to the mention.\n\n"
        "**Scoring formula** (lexical_weighted_coverage):\n"
        "- If the normalised mention matches a candidate name or alias exactly "
        "→ score = 1.0.\n"
        "- Otherwise: score = 0.6 × mention_coverage + 0.4 × candidate_coverage.\n\n"
        "**Alias handling**: each alias is scored independently; the best "
        "representation wins.\n\n"
        "**If the mention is already RESOLVED**, the response is HTTP 200 with an "
        "empty candidates list.\n\n"
        "**Ordering**: score descending, then canonical_name ascending, then "
        "entity_id ascending.\n\n"
        "This endpoint is read-only: it never modifies the mention's "
        "resolution_status or entity_id."
    ),
)
def get_mention_scored_candidates(
    mention_id: str,
    service: CandidateScoringService = Depends(get_candidate_scoring_service),
) -> ScoredCandidatesResponse:
    """Return scored candidate entities for the given mention."""
    try:
        mention, scored = service.get_scored_candidates(mention_id)
    except ScoringMentionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return _scored_candidates_to_response(mention, scored)


@router.post(
    "/mentions/{mention_id}/resolve",
    response_model=ResolutionDecisionResponse,
    summary="Resolve an entity mention",
    description=(
        "Apply the Resolution Decision Engine to an unresolved entity mention "
        "and return an explainable decision.\n\n"
        "The engine applies a deterministic threshold + margin policy to the "
        "scored candidates and produces one of three outcomes:\n\n"
        "- **RESOLVED**: the top candidate exceeded the confidence threshold and "
        "had sufficient margin over the second candidate.  The mention's "
        "entity_id is updated to the selected entity.\n"
        "- **AMBIGUOUS**: the top candidate exceeded the threshold but was too "
        "close to the second candidate.  No entity is assigned.\n"
        "- **UNRESOLVED**: no candidate met the confidence threshold.  "
        "No entity is assigned.\n\n"
        "**Score semantics**: top_score and second_score are lexical similarity "
        "scores in [0.0, 1.0].  They are NOT probabilities.  A score of 0.92 "
        "means the candidate received a lexical similarity score of 0.92 — not "
        "that there is a 92 % chance the entity is correct.\n\n"
        "**Idempotency**: if the mention is already RESOLVED, the engine returns "
        "the current state without modification (RESOLVED mentions are never "
        "downgraded).\n\n"
        "This endpoint may modify the mention's resolution_status and entity_id "
        "(only the Resolution Decision stage may do this)."
    ),
)
def resolve_mention(
    mention_id: str,
    service: ResolutionService = Depends(get_resolution_service),
) -> ResolutionDecisionResponse:
    """Apply the resolution decision engine and return the explainable decision."""
    try:
        decision = service.resolve(mention_id)
    except ResolutionMentionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return _decision_to_response(decision)


@router.get(
    "",
    response_model=list[EntityResponse],
    summary="List canonical entities",
    description=(
        "Return all canonical entities in the registry.  "
        "Optionally filter by entity_type (e.g. ?entity_type=PERSON)."
    ),
)
def list_entities(
    entity_type: EntityTypeSchema | None = Query(
        default=None,
        description="Filter results by entity type.",
    ),
    service: EntityService = Depends(get_entity_service),
) -> list[EntityResponse]:
    """Return all entities, optionally filtered by type."""
    domain_type = EntityType(entity_type.value) if entity_type is not None else None
    entities = service.list_entities(entity_type=domain_type)
    return [_entity_to_response(e) for e in entities]


@router.get(
    "/{entity_id}/insights",
    response_model=EntityInsightsResponse,
    summary="Retrieve derived insights for an entity",
    description=(
        "Return all derived insights for a canonical entity: actionable intelligence "
        "computed deterministically from the entity's temporal lifecycle history and "
        "organisational memory.\n\n"
        "**This endpoint answers: 'What changed for this entity, and which changes "
        "are important?'**\n\n"
        "**Insight types:**\n"
        "- **STATE_CHANGED**: entity moved from one valid state to another.\n"
        "- **ISSUE_BLOCKED**: entity entered BLOCKED state (in addition to STATE_CHANGED).\n"
        "- **ISSUE_RESOLVED**: entity entered RESOLVED state (in addition to STATE_CHANGED).\n"
        "- **REOPEN_ATTEMPT**: observation tried to reopen a RESOLVED entity (state unchanged).\n"
        "- **REPEATED_OBSERVATION**: entity observed multiple times in one meeting without progress.\n"
        "- **UNKNOWN_STATE**: entity has observations but no state-bearing evidence.\n"
        "- **STALE_ENTITY**: entity not observed for >= 30 days and not RESOLVED.\n\n"
        "**Severity mapping (deterministic):**\n"
        "- INFO: STATE_CHANGED, ISSUE_RESOLVED, REPEATED_OBSERVATION, UNKNOWN_STATE.\n"
        "- WARNING: ISSUE_BLOCKED, REOPEN_ATTEMPT, STALE_ENTITY.\n\n"
        "**Determinism**: the same repository state always produces the same insights "
        "in the same order.  Running this endpoint multiple times is idempotent.\n\n"
        "**Ordering**: (observed_at ASC, entity_id ASC, insight_type ASC, insight_id ASC).\n\n"
        "**Read-only guarantees:**\n"
        "- Only RESOLVED mentions participate (via Temporal State Engine).\n"
        "- This endpoint never creates or modifies entities, mentions, or resolution state.\n"
        "- It never re-runs extraction, candidate generation, scoring, or resolution.\n\n"
        "**Returns HTTP 200** with insight_count=0 and insights=[] when the entity "
        "exists but has no applicable insights.\n"
        "**Returns HTTP 404** if the entity_id does not exist."
    ),
)
def get_entity_insights(
    entity_id: str,
    service: InsightService = Depends(get_insight_service),
) -> EntityInsightsResponse:
    """Return derived insights for a canonical entity."""
    from datetime import datetime, timezone
    try:
        insights = service.get_entity_insights(
            entity_id=entity_id,
            current_time=datetime.now(timezone.utc),
        )
    except EntityNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return _insights_to_response(entity_id, insights)


@router.get(
    "/{entity_id}/attention",
    response_model=EntityAttentionDetailResponse,
    summary="Retrieve prioritised attention for an entity",
    description=(
        "Return the prioritised attention result for a specific canonical entity.\n\n"
        "**This endpoint answers: 'Does the organisation need to pay attention to "
        "this entity right now, and if so, why?'**\n\n"
        "The Attention Engine aggregates all applicable signals (STALE_ENTITY, "
        "REOPEN_ATTEMPT, ISSUE_BLOCKED, etc.) into a single numeric score and "
        "priority level (CRITICAL, HIGH, MEDIUM, LOW).\n\n"
        "**Rule F (Deduplication)**: Each reason (e.g. ENTITY_STALE) contributes to "
        "the score at most once per entity, regardless of how many individual "
        "observations trigger it.\n\n"
        "**Rule E (No zero-score entities)**: If the entity has no actionable signals "
        "(score = 0), `has_attention` is false and `attention` is null.\n\n"
        "**Returns HTTP 200** when the entity exists, regardless of whether it "
        "requires attention.\n"
        "**Returns HTTP 404** if the entity_id does not exist."
    ),
)
def get_entity_attention(
    entity_id: str,
    service: AttentionService = Depends(get_attention_service),
) -> EntityAttentionDetailResponse:
    """Return the prioritised attention result for a specific canonical entity."""
    from datetime import datetime, timezone
    try:
        attention = service.get_entity_attention(
            entity_id=entity_id,
            current_time=datetime.now(timezone.utc),
        )
    except EntityNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return _attention_to_entity_response(entity_id, attention)


@router.get(
    "/{entity_id}/actions",
    response_model=EntityActionsResponse,
    summary="Retrieve recommended actions for an entity",
    description=(
        "Return the recommended next actions for a specific canonical entity.\n\n"
        "**This endpoint answers: 'What should we do next regarding this entity?'**\n\n"
        "The Action Recommendation Engine derives actions from the entity's Prioritization "
        "and Attention signals.\n\n"
        "**Rules:**\n"
        "- BLOCKED/HIGH ATTENTION → ESCALATE\n"
        "- STALE ENTITY → REQUEST_UPDATE\n"
        "- REOPEN ATTEMPT → INVESTIGATE\n"
        "- REPEATED OBSERVATIONS → FOLLOW_UP\n"
        "- SIGNIFICANT STATE CHANGE → REVIEW\n\n"
        "**Returns HTTP 200** with an empty list if no action is required.\n"
        "**Returns HTTP 404** if the entity_id does not exist."
    ),
)
def get_entity_actions(
    entity_id: str,
    service: ActionRecommendationService = Depends(get_action_recommendation_service),
) -> EntityActionsResponse:
    """Return the recommended actions for a specific canonical entity."""
    from datetime import datetime, timezone
    try:
        actions = service.get_entity_actions(
            entity_id=entity_id,
            current_time=datetime.now(timezone.utc),
        )
    except EntityNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return _actions_to_response(entity_id, actions)


@router.get(
    "/{entity_id}/timeline",
    response_model=UnifiedEntityTimelineResponse,
    summary="Retrieve the unified chronological timeline for an entity",
    description=(
        "Return the complete chronological story for a specific canonical entity.\n\n"
        "**This endpoint answers: 'What is the complete story of this entity?'**\n\n"
        "It aggregates existing outputs from observations, state transitions, memory facts, "
        "insights, attention, and actions into a deterministic timeline.\n\n"
        "**Returns HTTP 404** if the entity_id does not exist."
    ),
)
def get_entity_timeline(
    entity_id: str,
    service: UnifiedTimelineService = Depends(get_unified_timeline_service),
) -> UnifiedEntityTimelineResponse:
    """Return the unified chronological timeline for a specific canonical entity."""
    from datetime import datetime, timezone
    try:
        timeline = service.get_unified_timeline(
            entity_id=entity_id,
            current_time=datetime.now(timezone.utc),
        )
    except EntityNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return _unified_timeline_to_response(timeline)


@router.get(
    "/{entity_id}/memory",
    response_model=EntityMemoryResponse,
    summary="Retrieve the organisational memory record for an entity",
    description=(
        "Return the structured organisational memory of a canonical entity: "
        "all evidence-backed facts the organisation knows about it, derived "
        "deterministically from its complete observation history.\n\n"
        "**This endpoint answers: 'What does the organisation currently know "
        "about this entity based on all available evidence?'**\n\n"
        "**Memory fact types:**\n"
        "- **FIRST_OBSERVED**: earliest resolved observation (meeting + mention).\n"
        "- **LAST_OBSERVED**: most recent observation (only when >= 2 exist).\n"
        "- **CURRENT_STATE**: current lifecycle state (aggregate — no single evidence source).\n"
        "- **STATE_TRANSITION**: each valid lifecycle state change, with evidence.\n"
        "- **REPEATED_OBSERVATION**: each meeting where entity appeared >= 2 times.\n\n"
        "**Evidence grounding**: every fact (except CURRENT_STATE) traces directly "
        "to a specific meeting and/or mention.\n\n"
        "**Read-only guarantees:**\n"
        "- Only RESOLVED mentions participate.\n"
        "- AMBIGUOUS and UNRESOLVED mentions are excluded.\n"
        "- This endpoint never creates or modifies entities, mentions, or resolution state.\n"
        "- It never re-runs extraction, candidate generation, scoring, or resolution.\n\n"
        "**Returns HTTP 200** with a valid memory object (facts=[CURRENT_STATE]) "
        "when the entity exists but has no resolved mentions.\n"
        "**Returns HTTP 404** if the entity_id does not exist."
    ),
)
def get_entity_memory(
    entity_id: str,
    service: OrganisationalMemoryService = Depends(get_organisational_memory_service),
) -> EntityMemoryResponse:
    """Return the organisational memory record for a canonical entity."""
    try:
        memory = service.get_entity_memory(entity_id)
    except EntityNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return _memory_to_response(memory)


@router.get(
    "/{entity_id}/temporal",
    response_model=EntityTimelineResponse,
    summary="Retrieve the temporal lifecycle timeline for an entity",
    description=(
        "Return the chronological temporal lifecycle timeline of a canonical entity: "
        "all resolved observations (mentions) of that entity across all meetings, "
        "interpreted for lifecycle state and ordered by meeting_date ascending.\n\n"
        "**This endpoint answers: 'How does the state of this entity evolve over time "
        "based on chronological observations?'**\n\n"
        "**State vocabulary:** UNKNOWN, OPEN, IN_PROGRESS, BLOCKED, RESOLVED.\n\n"
        "**State interpretation:** States are inferred from the source_text of each "
        "resolved mention using a deterministic keyword-based interpreter.  "
        "No LLM is used.\n\n"
        "**Transition policy:** Only valid state transitions are applied.  "
        "Invalid transitions (e.g., RESOLVED to IN_PROGRESS) are recorded in the "
        "timeline with is_valid_transition=false but do not change the current state.\n\n"
        "**Resolution safety rules:**\n"
        "- Only RESOLVED mentions participate (entity_id != null, "
        "resolution_status=RESOLVED).\n"
        "- AMBIGUOUS and UNRESOLVED mentions are excluded.\n\n"
        "**Ordering:** (meeting_date ASC, meeting_id ASC, mention_id ASC) — "
        "fully deterministic, using only real data fields.\n\n"
        "**Returns HTTP 200** with an empty timeline and current_state='UNKNOWN' "
        "when the entity exists but has no resolved mentions.\n"
        "**Returns HTTP 404** if the entity_id does not exist.\n\n"
        "This endpoint is read-only: it never creates or modifies entities, "
        "mentions, resolution state, or triggers any pipeline stage."
    ),
)
def get_entity_timeline(
    entity_id: str,
    service: TemporalStateService = Depends(get_temporal_state_service),
) -> EntityTimelineResponse:
    """Return the temporal lifecycle timeline for a canonical entity."""
    try:
        timeline = service.get_entity_timeline(entity_id)
    except EntityNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return _timeline_to_response(timeline)


@router.get(
    "/{entity_id}/correlations",
    response_model=EntityCorrelationResponse,
    summary="Retrieve cross-meeting correlation history for an entity",
    description=(
        "Return the chronological cross-meeting history of a canonical entity: "
        "all resolved observations (mentions) of that entity across all meetings, "
        "ordered by meeting_date ascending.\n\n"
        "**This endpoint answers: 'What observations involving this entity exist "
        "across different meetings?'** — not 'Who is this entity?' (that is "
        "entity resolution).\n\n"
        "**Resolution safety rules:**\n"
        "- Only RESOLVED mentions participate (entity_id != null, "
        "resolution_status=RESOLVED).\n"
        "- AMBIGUOUS and UNRESOLVED mentions are excluded.\n\n"
        "**Ordering:** (meeting_date ASC, meeting_id ASC, mention_id ASC) — "
        "fully deterministic, using only real data fields.\n\n"
        "**Returns HTTP 200** with an empty observations list when the entity "
        "exists but has no resolved mentions.\n"
        "**Returns HTTP 404** if the entity_id does not exist.\n\n"
        "This endpoint is read-only: it never creates or modifies entities, "
        "mentions, or resolution state."
    ),
)
def get_entity_correlations(
    entity_id: str,
    service: CorrelationService = Depends(get_correlation_service),
) -> EntityCorrelationResponse:
    """Return the cross-meeting correlation history for a canonical entity."""
    try:
        correlation = service.get_entity_correlations(entity_id)
    except EntityNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return _correlation_to_response(correlation)


@router.get(
    "/{entity_id}",
    response_model=EntityResponse,
    summary="Retrieve a canonical entity by ID",
    description="Fetch a canonical entity by its unique identifier.",
)
def get_entity(
    entity_id: str,
    service: EntityService = Depends(get_entity_service),
) -> EntityResponse:
    """Return a canonical entity by ID or raise HTTP 404."""
    try:
        entity = service.get_entity(entity_id)
    except EntityNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return _entity_to_response(entity)
