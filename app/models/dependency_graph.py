"""Domain models for the Dependency Graph Intelligence layer (Stage 16).

These models represent the results of graph traversal across explicit
dependency relationships (DEPENDS_ON and BLOCKS).

Design notes
------------
- All models are read-only value types.
- DependencyEdge is a single traversal step with full evidence traceability.
- DependencyPath is a chain of edges from a root entity to a reachable entity.
- DependencyGraph is the full result of traversal from one root entity.
- No entity objects are embedded inside graph nodes — only entity_id strings
  are used as node identities, matching the existing repository architecture.
- path_id is deterministic: sha256(":".join(entity_path))[:16].

IMPORTANT SEMANTIC CONSTRAINTS:
- CO_OCCURS_WITH is NEVER a traversal edge.
- Traversal only uses DEPENDS_ON and BLOCKS edges.
- A DependencyPath is DIRECT when depth==1 and TRANSITIVE when depth>1.
- Evidence (source_text, mention_id, related_meeting_ids) is preserved from
  the underlying ExplicitDependency records — never fabricated.
- Cycle detection terminates traversal without duplicating nodes.

Relationship semantics are unchanged from Stage 15.1:
  DEPENDS_ON: source REQUIRES target.
  BLOCKS: source IS BLOCKING target.
"""

import hashlib
from typing import Optional

from pydantic import BaseModel, Field

from app.models.relationships import RelationshipType


# ---------------------------------------------------------------------------
# DependencyEdge
# ---------------------------------------------------------------------------

class DependencyEdge(BaseModel):
    """A single directed edge in the dependency graph traversal.

    Represents one step in a dependency chain: from source_entity_id
    to target_entity_id via relationship_type.

    All evidence fields are carried through from the underlying
    ExplicitDependency records — no fabrication.
    """

    source_entity_id: str = Field(
        ...,
        description="The entity at the start of this edge.",
    )
    target_entity_id: str = Field(
        ...,
        description="The entity at the end of this edge.",
    )
    relationship_type: RelationshipType = Field(
        ...,
        description="DEPENDS_ON or BLOCKS. CO_OCCURS_WITH never appears here.",
    )
    strength: int = Field(
        ...,
        ge=1,
        description="Number of distinct meetings that stated this relationship.",
    )
    related_meeting_ids: list[str] = Field(
        default_factory=list,
        description="IDs of meetings that provide evidence for this edge.",
    )
    source_text: Optional[str] = Field(
        default=None,
        description="Verbatim transcript excerpt providing the primary evidence.",
    )
    mention_id: Optional[str] = Field(
        default=None,
        description="ID of the primary evidence mention.",
    )


# ---------------------------------------------------------------------------
# DependencyPath
# ---------------------------------------------------------------------------

class DependencyPath(BaseModel):
    """A traversal path from a root entity to a reachable entity.

    A DependencyPath captures a chain of explicit dependency relationships
    that connects `start_entity_id` to `end_entity_id` through
    `entity_path`.

    Terminology
    -----------
    DIRECT (is_direct=True):
        depth == 1.  The root entity is a direct participant in a single
        DEPENDS_ON or BLOCKS relationship with end_entity_id.

    TRANSITIVE (is_transitive=True):
        depth >= 2.  end_entity_id is reachable from start_entity_id through
        one or more intermediate entities.

    Evidence
    --------
    Each edge in `edges` carries the full evidence (source_text, mention_id,
    meeting IDs) from the underlying ExplicitDependency records.

    Determinism
    -----------
    path_id is sha256(":".join(entity_path))[:16].  The same entity_path
    always produces the same path_id.
    """

    path_id: str = Field(
        ...,
        description=(
            "Deterministic identifier for this path. "
            "sha256(':'.join(entity_path))[:16]."
        ),
    )
    start_entity_id: str = Field(
        ...,
        description="The root entity from which traversal started.",
    )
    end_entity_id: str = Field(
        ...,
        description="The entity at the end of this path.",
    )
    depth: int = Field(
        ...,
        ge=1,
        description=(
            "The number of hops from start to end. "
            "depth=1 means direct; depth>=2 means transitive."
        ),
    )
    entity_path: list[str] = Field(
        ...,
        description=(
            "Ordered list of entity_ids from start_entity_id to end_entity_id "
            "(inclusive). Length = depth + 1."
        ),
    )
    relationship_path: list[RelationshipType] = Field(
        ...,
        description=(
            "Ordered list of relationship types corresponding to each hop. "
            "Length = depth."
        ),
    )
    edges: list[DependencyEdge] = Field(
        ...,
        description="The individual edges constituting this path.",
    )
    is_direct: bool = Field(
        ...,
        description="True when depth == 1.",
    )
    is_transitive: bool = Field(
        ...,
        description="True when depth >= 2.",
    )


def make_path_id(entity_path: list[str]) -> str:
    """Generate a deterministic path_id from an ordered entity_path list.

    sha256(':'.join(entity_path))[:16]
    """
    raw = ":".join(entity_path)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# DependencyGraph
# ---------------------------------------------------------------------------

class DependencyGraph(BaseModel):
    """The complete dependency graph result for one root entity.

    Produced by DependencyGraphService.build_dependency_graph().

    Contains all DependencyPaths reachable from root_entity_id within
    the configured max_depth, separated into direct and transitive groups.

    Cycle handling
    --------------
    When a cycle is detected (an entity already in the current entity_path
    would be visited again), traversal terminates that branch without
    recursing further.  The `contains_cycle` flag is set True and
    `cycle_entity_ids` lists the entities that would close cycles.

    No path is fabricated for cycle edges — the valid path up to the cycle
    is recorded and the cycle boundary is noted.

    Diamond graphs
    --------------
    Multiple paths to the same entity are both preserved (as separate
    DependencyPath objects).  However, `all_reachable_entity_ids` is a
    deduplicated sorted list — each entity_id appears at most once.
    """

    root_entity_id: str = Field(
        ...,
        description="The entity from which this graph was built.",
    )
    direct_dependencies: list[DependencyPath] = Field(
        default_factory=list,
        description=(
            "Paths with depth==1: entities that root_entity_id directly "
            "depends on (DEPENDS_ON or BLOCKS, outgoing from root)."
        ),
    )
    transitive_dependencies: list[DependencyPath] = Field(
        default_factory=list,
        description=(
            "Paths with depth>=2: entities reachable from root_entity_id "
            "through intermediate entities."
        ),
    )
    all_reachable_entity_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Deduplicated, sorted list of all entity_ids reachable from "
            "root_entity_id (excluding root itself)."
        ),
    )
    max_depth_reached: int = Field(
        ...,
        description="The maximum depth of any path found during traversal.",
    )
    contains_cycle: bool = Field(
        default=False,
        description="True if any cycle was detected during traversal.",
    )
    cycle_entity_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Sorted list of entity_ids that would close a cycle "
            "(i.e., entities already in the current path when encountered again)."
        ),
    )
