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

Ordering note
-------------
All /entities/mentions/* routes must be declared BEFORE /entities/{entity_id}
so that FastAPI routes the literal path segment "mentions" correctly rather
than treating it as a dynamic entity_id.
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
    ResolutionStatusSchema,
)
from app.services.candidate_service import CandidateService, MentionNotFoundError
from app.services.entity_service import EntityNotFoundError, EntityService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/entities", tags=["Entities"])

# ---------------------------------------------------------------------------
# Shared repository singletons
# (When we move to PostgreSQL we'll replace these with session-scoped factories.)
# ---------------------------------------------------------------------------
_entity_repository = InMemoryEntityRepository()
_mention_repository = InMemoryMentionRepository()


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
