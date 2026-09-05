"""Internal domain models for the Cross-Entity Risk Propagation & Impact Analysis Engine (Stage 13).

These models represent deterministic, read-only associations where risks from one entity
(the source) are propagated to strongly connected entities (the impacted entities).

IMPORTANT ARCHITECTURAL CONSTRAINT:
Impact associations are NOT causal dependencies. "Entity B is impacted by Entity A"
means they are associated and Entity A has a risk signal. It does NOT mean Entity A blocks Entity B.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class RiskSignalType(str, Enum):
    """The type of risk signal that triggered the impact propagation."""
    
    BLOCKED_ENTITY = "BLOCKED_ENTITY"
    CRITICAL_ATTENTION = "CRITICAL_ATTENTION"
    HIGH_ATTENTION = "HIGH_ATTENTION"
    REOPEN_ATTEMPT = "REOPEN_ATTEMPT"
    STALE_ENTITY = "STALE_ENTITY"
    REPEATED_OBSERVATION = "REPEATED_OBSERVATION"


class ImpactLevel(str, Enum):
    """The severity of the propagated impact."""
    
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class EntityImpact(BaseModel):
    """A deterministic record of risk propagation from one entity to another."""

    impact_id: str = Field(
        ..., 
        description="Deterministic identifier derived from a SHA-256 hash."
    )
    
    source_entity_id: str = Field(
        ...,
        description="The entity carrying the original risk signal (e.g., the BLOCKED entity)."
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
        description="Deduplicated, sorted list of risk signals from the source entity."
    )
    
    relationship_strength: int = Field(
        ...,
        ge=1,
        description="The strength of the relationship (number of shared meetings)."
    )
    
    related_meeting_ids: list[str] = Field(
        default_factory=list,
        description="IDs of meetings that provide evidence for the relationship."
    )
    
    reason: str = Field(
        ...,
        description="Human-readable explanation of why this impact was generated, explicitly avoiding causal language."
    )
    
    generated_from_at: datetime = Field(
        ...,
        description="The timestamp when this impact was evaluated."
    )
    
    deterministic_sort_key: str = Field(
        ...,
        description="Stable key for repeatable ordering."
    )
