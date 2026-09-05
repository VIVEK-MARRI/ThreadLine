"""API schemas for the Cross-Entity Risk Propagation & Impact Analysis Engine (Stage 13).

These Pydantic schemas define the public JSON contract for the impacts API endpoint.
They mirror the internal domain models but provide stable external representations.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.impact import ImpactLevel, RiskSignalType


class EntityImpactSchema(BaseModel):
    """Public representation of a risk impact association."""

    impact_id: str = Field(
        ...,
        description="Deterministic identifier for this impact record."
    )
    source_entity_id: str = Field(
        ...,
        description="The entity carrying the original risk signal."
    )
    impacted_entity_id: str = Field(
        ...,
        description="The entity associated with the source entity."
    )
    impact_level: ImpactLevel = Field(
        ...,
        description="The computed severity of this impact."
    )
    risk_signals: list[RiskSignalType] = Field(
        default_factory=list,
        description="The risk signals from the source entity."
    )
    relationship_strength: int = Field(
        ...,
        description="The strength of the relationship (number of shared meetings)."
    )
    related_meeting_ids: list[str] = Field(
        default_factory=list,
        description="IDs of meetings providing evidence for the relationship."
    )
    reason: str = Field(
        ...,
        description="Human-readable explanation of why this impact was generated."
    )
    generated_from_at: datetime = Field(
        ...,
        description="The timestamp when this impact was evaluated."
    )
    deterministic_sort_key: str = Field(
        ...,
        description="Stable key ensuring repeatable ordering in responses."
    )


class EntityImpactResponse(BaseModel):
    """API response for the impacts endpoint."""

    entity_id: str = Field(
        ...,
        description="The canonical entity whose impacts are being queried."
    )
    impact_count: int = Field(
        ...,
        description="Total number of impacts affecting this entity."
    )
    impacts: list[EntityImpactSchema] = Field(
        default_factory=list,
        description="The impact associations directed at this entity."
    )
