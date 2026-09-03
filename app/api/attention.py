"""Attention API router.

Handles HTTP concerns for the top-level GET /attention endpoint.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.entities import get_attention_service
from app.schemas.attention import AttentionResponse, EntityAttentionSchema
from app.services.attention_service import AttentionService

router = APIRouter(prefix="/attention", tags=["Attention"])


@router.get(
    "",
    response_model=AttentionResponse,
    summary="Get prioritised attention across all entities",
    description=(
        "Return a prioritised, sorted list of all canonical entities that require "
        "organisational attention based on deterministic lifecycle and memory rules.\n\n"
        "**Ordering**: CRITICAL items first, then HIGH, MEDIUM, LOW. "
        "Ties are broken by highest numeric score, then entity_id.\n\n"
        "**Rule F (Deduplication)**: Each entity appears at most once, with all its "
        "applicable signals aggregated into a single score and level.\n\n"
        "**Rule E (No zero-score entities)**: Entities that are fully resolved or "
        "have no state-bearing observations do not appear in this list (score = 0).\n\n"
        "This endpoint evaluates current state dynamically on read and never modifies data."
    ),
)
def get_attention(
    service: Annotated[AttentionService, Depends(get_attention_service)],
) -> AttentionResponse:
    """Return all prioritised attention items across the repository."""
    # current_time is omitted to use the service's default of datetime.now(utc)
    results = service.get_attention()

    schemas = [
        EntityAttentionSchema(
            attention_id=a.attention_id,
            entity_id=a.entity_id,
            attention_level=a.attention_level.value,  # type: ignore[arg-type]
            score=a.score,
            reasons=[r.value for r in a.reasons],  # type: ignore[misc]
            related_insight_ids=a.related_insight_ids,
            evaluated_at=a.evaluated_at,
        )
        for a in results
    ]

    return AttentionResponse(
        entity_count=len(schemas),
        items=schemas,
    )
