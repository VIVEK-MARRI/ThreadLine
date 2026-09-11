from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.relationships import RelationshipTypeSchema


class DependencyEdgeSchema(BaseModel):
    """Schema for a single directed edge in the dependency graph traversal."""
    source_entity_id: str
    target_entity_id: str
    relationship_type: RelationshipTypeSchema
    strength: int
    related_meeting_ids: list[str] = Field(default_factory=list)
    source_text: Optional[str] = None
    mention_id: Optional[str] = None


class DependencyPathSchema(BaseModel):
    """Schema for a traversal path from a root entity to a reachable entity."""
    path_id: str
    start_entity_id: str
    end_entity_id: str
    depth: int
    entity_path: list[str]
    relationship_path: list[RelationshipTypeSchema]
    edges: list[DependencyEdgeSchema]
    is_direct: bool
    is_transitive: bool


class DependencyGraphResponse(BaseModel):
    """Response schema for the complete dependency graph result for one root entity."""
    root_entity_id: str
    direct_dependencies: list[DependencyPathSchema] = Field(default_factory=list)
    transitive_dependencies: list[DependencyPathSchema] = Field(default_factory=list)
    all_reachable_entity_ids: list[str] = Field(default_factory=list)
    max_depth_reached: int
    contains_cycle: bool = False
    cycle_entity_ids: list[str] = Field(default_factory=list)
