"""Pydantic schemas for the Entity Relationship API.

These are the public contracts for the relationship graph endpoints. They are
deliberately kept separate from internal domain models.
"""

from enum import Enum

from pydantic import BaseModel, Field


class RelationshipTypeSchema(str, Enum):
    """Relationship types returned by the API."""

    CO_OCCURS_WITH = "CO_OCCURS_WITH"
    RELATED_TO = "RELATED_TO"


class RelationshipEvidenceTypeSchema(str, Enum):
    """Evidence types for relationships returned by the API."""

    CO_OCCURRENCE = "CO_OCCURRENCE"


class EntityRelationshipSchema(BaseModel):
    """A deterministic relationship between two canonical entities."""

    relationship_id: str = Field(..., description="Deterministic identifier for this relationship.")
    source_entity_id: str = Field(..., description="The entity from which this relationship originates.")
    target_entity_id: str = Field(..., description="The entity to which this relationship points.")
    relationship_type: RelationshipTypeSchema = Field(..., description="The semantic meaning of this relationship.")
    evidence_type: RelationshipEvidenceTypeSchema = Field(..., description="The type of evidence justifying this relationship.")
    evidence: str = Field(..., description="Human-readable summary of the evidence.")
    related_meeting_ids: list[str] = Field(default_factory=list, description="IDs of meetings providing evidence.")
    strength: int = Field(..., description="Deterministic strength score (e.g., meeting co-occurrence count).")
    deterministic_sort_key: str = Field(..., description="Stable key for repeatable ordering.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "relationship_id": "8a451e5e-5b12-5813-bfa3-0f1112423377",
                    "source_entity_id": "entity_001",
                    "target_entity_id": "entity_002",
                    "relationship_type": "CO_OCCURS_WITH",
                    "evidence_type": "CO_OCCURRENCE",
                    "evidence": "Entities co-occurred in 3 meeting(s).",
                    "related_meeting_ids": ["meeting_1", "meeting_2", "meeting_3"],
                    "strength": 3,
                    "deterministic_sort_key": "000003_CO_OCCURS_WITH_entity_002_8a451e5e-5b12-5813-bfa3-0f1112423377"
                }
            ]
        }
    }


class EntityRelationshipGraphResponse(BaseModel):
    """Response body for the entity relationship graph endpoint."""

    entity_id: str = Field(..., description="The central entity of this graph.")
    relationship_count: int = Field(..., description="Total number of relationships.")
    related_entity_ids: list[str] = Field(default_factory=list, description="IDs of all other related entities.")
    relationships: list[EntityRelationshipSchema] = Field(default_factory=list, description="All deterministic relationships.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "entity_id": "entity_001",
                    "relationship_count": 1,
                    "related_entity_ids": ["entity_002"],
                    "relationships": [
                        {
                            "relationship_id": "8a451e5e-5b12-5813-bfa3-0f1112423377",
                            "source_entity_id": "entity_001",
                            "target_entity_id": "entity_002",
                            "relationship_type": "CO_OCCURS_WITH",
                            "evidence_type": "CO_OCCURRENCE",
                            "evidence": "Entities co-occurred in 3 meeting(s).",
                            "related_meeting_ids": ["meeting_1", "meeting_2", "meeting_3"],
                            "strength": 3,
                            "deterministic_sort_key": "000003_CO_OCCURS_WITH_entity_002_8a451e5e-5b12-5813-bfa3-0f1112423377"
                        }
                    ]
                }
            ]
        }
    }
