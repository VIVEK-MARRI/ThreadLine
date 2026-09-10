"""Portfolio API router.

Handles HTTP concerns only: routing, request parsing, response serialisation,
and HTTP error translation.  All business logic lives in PortfolioIntelligenceService.

Route table
-----------
GET    /portfolio    Return the organisation-wide portfolio snapshot (read-only)

This endpoint is strictly read-only.  No POST/PUT/PATCH/DELETE endpoints exist
because the portfolio is computed on demand and never persisted.

The portfolio is deterministic: the same repository state and the same
evaluation time always produce the same response.

Dependency wiring
-----------------
We import get_portfolio_intelligence_service from app.api.entities (the same
pattern that app.api.attention uses for get_attention_service).  This ensures
the portfolio service uses the SAME shared in-memory repository singletons as
all other API endpoints.
"""

from fastapi import APIRouter, Depends

from app.api.entities import get_portfolio_intelligence_service
from app.schemas.portfolio import (
    OrganisationPortfolioResponse,
    PortfolioEntitySummarySchema,
    PortfolioRiskLevelSchema,
)
from app.services.portfolio_intelligence_service import PortfolioIntelligenceService

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


@router.get(
    "",
    response_model=OrganisationPortfolioResponse,
    summary="Get the organisation-wide portfolio snapshot",
    description=(
        "Return a deterministic, read-only snapshot of the organisation's entity "
        "risk posture, aggregated from all existing ThreadLine intelligence "
        "(attention, insights, actions, temporal state, and impact analysis).\\n\\n"
        "**Risk classification** (transparent, deterministic rules):\\n"
        "- **CRITICAL**: entity has CRITICAL attention OR a CRITICAL impact signal.\\n"
        "- **HIGH**: entity has HIGH attention, is BLOCKED, OR has a HIGH impact signal.\\n"
        "- **MEDIUM**: entity has MEDIUM attention, a MEDIUM impact signal, OR active "
        "insights and actions.\\n"
        "- **LOW**: entity has observations but no stronger risk condition applies.\\n\\n"
        "**Important semantic constraints**:\\n"
        "- CO_OCCURS_WITH relationships do NOT imply dependency or causation.\\n"
        "- 'Associated risk' and 'affected entities' language is used; "
        "'blocks' or 'depends on' are not.\\n"
        "- No ownership, department, or financial data is included.\\n\\n"
        "**Ordering**: risk_level DESC, attention_score DESC, impact_count DESC, "
        "action_count DESC, entity_id ASC.\\n\\n"
        "This endpoint evaluates current state dynamically on read and never modifies data."
    ),
)
def get_portfolio(
    service: PortfolioIntelligenceService = Depends(get_portfolio_intelligence_service),
) -> OrganisationPortfolioResponse:
    """Return the organisation-wide portfolio snapshot."""
    # current_time is omitted to use datetime.now(utc) as default,
    # consistent with GET /api/v1/attention and other read endpoints.
    portfolio = service.get_portfolio()

    entity_schemas = [
        PortfolioEntitySummarySchema(
            entity_id=e.entity_id,
            entity_type=e.entity_type,
            canonical_name=e.canonical_name,
            risk_level=PortfolioRiskLevelSchema(e.risk_level.value),
            attention_level=e.attention_level,
            attention_score=e.attention_score,
            impact_count=e.impact_count,
            action_count=e.action_count,
            active_insight_count=e.active_insight_count,
            current_state=e.current_state,
            observation_count=e.observation_count,
        )
        for e in portfolio.entities
    ]

    return OrganisationPortfolioResponse(
        total_entities=portfolio.total_entities,
        critical_entities=portfolio.critical_entities,
        high_risk_entities=portfolio.high_risk_entities,
        medium_risk_entities=portfolio.medium_risk_entities,
        low_risk_entities=portfolio.low_risk_entities,
        entities_with_active_actions=portfolio.entities_with_active_actions,
        entities_with_impact=portfolio.entities_with_impact,
        blocked_entities=portfolio.blocked_entities,
        entities=entity_schemas,
        evaluated_at=portfolio.evaluated_at,
    )
