"""Dependency Graph Intelligence Service (Stage 16).

Implements graph traversal over explicit DEPENDS_ON and BLOCKS relationships
to discover direct and transitive dependency chains.

Architecture
------------
DependencyGraphService is a READ-ONLY service.  It reads from the dependency
repository and entity repository — it never modifies any state.

The graph is built from ExplicitDependency records already stored by
DependencyResolutionService (Stage 15).  No new evidence is created here.

Traversal algorithm
-------------------
Iterative DFS with per-branch cycle detection.

For each traversal step from node N at depth D:
  1. Fetch all outgoing DEPENDS_ON and BLOCKS edges from the dependency repo
     where source_entity_id == N.
  2. Deduplicate logical edges: same (source, target, type) → one edge
     (aggregated meeting evidence, strength = distinct meeting count).
  3. Sort edges deterministically by (source_id, target_id, rel_type).
  4. For each edge (N → M):
     a. If M is already in the current entity_path → cycle detected, skip.
     b. If entity M does not exist in the entity_repo → skip (unresolved ref).
     c. If depth + 1 > max_depth → skip (depth limit enforced).
     d. Otherwise: push (M, depth+1, entity_path+[M], ...) onto stack.

Cycle detection
---------------
Cycle detection is per-branch: each DFS branch carries its own entity_path.
When the traversal tries to visit an entity that is already in the current
path, the branch is terminated and the entity is added to `cycle_entity_ids`.
This prevents infinite loops while preserving valid paths up to the cycle.

Diamond graph handling
----------------------
Multiple independent paths to the same entity are each preserved as
separate DependencyPath objects.  `all_reachable_entity_ids` is deduplicated.

Multiple paths to the same entity are NOT collapsed — both paths are
presented to callers for evidence traceability.

Deduplication of DependencyPath objects uses path_id (sha256 of entity_path).
If two branches produce the same exact entity_path, only one is kept.

CO_OCCURS_WITH exclusion
------------------------
CO_OCCURS_WITH relationships are NEVER traversal edges.  The repository query
`list_by_source_entity_id` returns ExplicitDependency records, which can only
be DEPENDS_ON or BLOCKS by construction (CO_OCCURS_WITH is never stored as
an ExplicitDependency).

Constants
---------
DEFAULT_MAX_DEPTH = 3
MAX_ALLOWED_DEPTH = 10 (API cap)

Semantic constraints
--------------------
- Only DEPENDS_ON and BLOCKS are traversal edges.
- A missed traversal is preferable to a fabricated edge.
- If entity_repo.get_by_id(M) is None, skip silently.
- source_text, mention_id, and meeting evidence are preserved from the
  underlying ExplicitDependency records.
"""

import logging
from typing import Optional

from app.models.dependency import ExplicitDependency
from app.models.dependency_graph import (
    DependencyEdge,
    DependencyGraph,
    DependencyPath,
    make_path_id,
)
from app.models.relationships import RelationshipType
from app.repositories.dependency_repository import AbstractDependencyRepository
from app.repositories.entity_repository import AbstractEntityRepository
from app.services.entity_service import EntityNotFoundError

logger = logging.getLogger(__name__)

# Traversal configuration
DEFAULT_MAX_DEPTH: int = 3
MAX_ALLOWED_DEPTH: int = 10

# Only these relationship types are used as traversal edges.
# CO_OCCURS_WITH is explicitly excluded.
_TRAVERSAL_RELATIONSHIP_TYPES = frozenset([
    RelationshipType.DEPENDS_ON,
    RelationshipType.BLOCKS,
])


def _make_edge_key(dep: ExplicitDependency) -> str:
    """Deterministic sort key for an ExplicitDependency record."""
    return f"{dep.source_entity_id}:{dep.target_entity_id}:{dep.relationship_type.value}"


def _aggregate_edges(
    deps: list[ExplicitDependency],
) -> dict[tuple[str, str, RelationshipType], list[ExplicitDependency]]:
    """Group ExplicitDependency records by logical edge (source, target, type).

    Returns a dict keyed by (source_entity_id, target_entity_id, relationship_type)
    with all ExplicitDependency records that share the same logical edge.
    This is used to compute strength (distinct meeting count) and primary evidence.
    """
    grouped: dict[tuple[str, str, RelationshipType], list[ExplicitDependency]] = {}
    for dep in deps:
        key = (dep.source_entity_id, dep.target_entity_id, dep.relationship_type)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(dep)
    return grouped


def _build_dependency_edge(
    source_entity_id: str,
    target_entity_id: str,
    relationship_type: RelationshipType,
    group: list[ExplicitDependency],
) -> DependencyEdge:
    """Build a DependencyEdge from an aggregated group of ExplicitDependency records.

    strength = number of distinct meetings.
    primary evidence = record with earliest meeting_id (deterministic).
    """
    meeting_ids = sorted({dep.meeting_id for dep in group})
    strength = len(meeting_ids)
    # Primary evidence: earliest meeting_id
    primary = sorted(group, key=lambda d: d.meeting_id)[0]

    return DependencyEdge(
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        relationship_type=relationship_type,
        strength=strength,
        related_meeting_ids=meeting_ids,
        source_text=primary.source_text,
        mention_id=primary.mention_id,
    )


class DependencyGraphService:
    """Provides graph traversal over explicit dependency relationships.

    This service is READ-ONLY.  It never modifies any repository state.

    Constructor
    -----------
    dependency_repo:
        Repository of ExplicitDependency records (DEPENDS_ON, BLOCKS).
    entity_repo:
        Repository of canonical entities.  Used to validate that traversal
        targets actually exist — missing entities are silently skipped.
    """

    def __init__(
        self,
        dependency_repo: AbstractDependencyRepository,
        entity_repo: AbstractEntityRepository,
    ) -> None:
        self._dependency_repo = dependency_repo
        self._entity_repo = entity_repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_dependency_graph(
        self,
        entity_id: str,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> DependencyGraph:
        """Build the complete dependency graph for a root entity.

        Traverses all outgoing DEPENDS_ON and BLOCKS edges from entity_id
        up to max_depth hops.

        Parameters
        ----------
        entity_id:
            The root entity to start traversal from.
        max_depth:
            Maximum number of hops to traverse.  Must be >= 1.
            Use DEFAULT_MAX_DEPTH (3) unless the caller specifies otherwise.

        Returns
        -------
        DependencyGraph
            Complete graph with direct and transitive dependency paths.

        Raises
        ------
        EntityNotFoundError
            If entity_id does not exist in the entity repository.
        """
        if self._entity_repo.get_by_id(entity_id) is None:
            raise EntityNotFoundError(f"Entity '{entity_id}' not found.")

        max_depth = max(1, min(max_depth, MAX_ALLOWED_DEPTH))

        paths, cycle_ids = self._traverse(entity_id, max_depth)

        direct = [p for p in paths if p.is_direct]
        transitive = [p for p in paths if p.is_transitive]

        # Deduplicate all_reachable_entity_ids
        all_reachable = sorted({p.end_entity_id for p in paths})

        max_depth_reached = max((p.depth for p in paths), default=0)

        return DependencyGraph(
            root_entity_id=entity_id,
            direct_dependencies=direct,
            transitive_dependencies=transitive,
            all_reachable_entity_ids=all_reachable,
            max_depth_reached=max_depth_reached,
            contains_cycle=bool(cycle_ids),
            cycle_entity_ids=sorted(cycle_ids),
        )

    def get_direct_dependencies(self, entity_id: str) -> list[DependencyPath]:
        """Return only depth=1 paths (direct outgoing dependencies).

        Parameters
        ----------
        entity_id:
            The entity whose direct dependencies to query.

        Returns
        -------
        list[DependencyPath]
            Direct dependency paths (depth == 1).  Empty list if none.

        Raises
        ------
        EntityNotFoundError
            If entity_id does not exist.
        """
        graph = self.build_dependency_graph(entity_id, max_depth=1)
        return graph.direct_dependencies

    def get_direct_dependents(self, entity_id: str) -> list[DependencyPath]:
        """Return the entities that DIRECTLY depend on entity_id.

        This is the inverse of get_direct_dependencies: instead of asking
        "what does entity_id depend on?", it asks "what depends on entity_id?"

        A DependencyPath is returned for each entity X where:
            X DEPENDS_ON entity_id, or
            X BLOCKS entity_id.

        The path goes from X (start_entity_id) to entity_id (end_entity_id).

        Parameters
        ----------
        entity_id:
            The entity whose direct dependents to query.

        Returns
        -------
        list[DependencyPath]
            Paths from dependents to entity_id (depth == 1).  Empty if none.

        Raises
        ------
        EntityNotFoundError
            If entity_id does not exist.
        """
        if self._entity_repo.get_by_id(entity_id) is None:
            raise EntityNotFoundError(f"Entity '{entity_id}' not found.")

        # Get all deps where entity_id is the TARGET
        incoming_deps = self._dependency_repo.list_by_target_entity_id(entity_id)

        # Aggregate by logical edge
        grouped = _aggregate_edges(incoming_deps)

        paths: list[DependencyPath] = []
        seen_path_ids: set[str] = set()

        for (src_id, tgt_id, rel_type), group in sorted(
            grouped.items(),
            key=lambda kv: f"{kv[0][0]}:{kv[0][1]}:{kv[0][2].value}",
        ):
            # Validate that the source entity exists
            if self._entity_repo.get_by_id(src_id) is None:
                continue

            edge = _build_dependency_edge(src_id, tgt_id, rel_type, group)
            entity_path = [src_id, tgt_id]
            pid = make_path_id(entity_path)

            if pid in seen_path_ids:
                continue
            seen_path_ids.add(pid)

            path = DependencyPath(
                path_id=pid,
                start_entity_id=src_id,
                end_entity_id=tgt_id,
                depth=1,
                entity_path=entity_path,
                relationship_path=[rel_type],
                edges=[edge],
                is_direct=True,
                is_transitive=False,
            )
            paths.append(path)

        return paths

    def get_transitive_dependencies(
        self,
        entity_id: str,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> list[DependencyPath]:
        """Return only depth>=2 paths (transitive dependencies).

        Parameters
        ----------
        entity_id:
            The entity whose transitive dependencies to query.
        max_depth:
            Maximum traversal depth.

        Returns
        -------
        list[DependencyPath]
            Transitive dependency paths (depth >= 2).  Empty if none or
            if the chain does not extend beyond direct relationships.

        Raises
        ------
        EntityNotFoundError
            If entity_id does not exist.
        """
        graph = self.build_dependency_graph(entity_id, max_depth=max_depth)
        return graph.transitive_dependencies

    # ------------------------------------------------------------------
    # Internal traversal
    # ------------------------------------------------------------------

    def _get_outgoing_edges(self, entity_id: str) -> list[DependencyEdge]:
        """Return aggregated outgoing dependency edges for entity_id.

        Aggregates multiple ExplicitDependency records for the same logical
        edge (same source, target, type across multiple meetings) into a
        single DependencyEdge with strength = distinct meeting count.

        CO_OCCURS_WITH is never stored in ExplicitDependency, so no
        explicit filtering is needed — but the check is present for safety.
        """
        raw = self._dependency_repo.list_by_source_entity_id(entity_id)
        # Filter to traversal types only (safety guard)
        raw = [
            d for d in raw
            if d.relationship_type in _TRAVERSAL_RELATIONSHIP_TYPES
        ]

        grouped = _aggregate_edges(raw)

        edges: list[DependencyEdge] = []
        for (src_id, tgt_id, rel_type), group in sorted(
            grouped.items(),
            key=lambda kv: f"{kv[0][0]}:{kv[0][1]}:{kv[0][2].value}",
        ):
            edges.append(_build_dependency_edge(src_id, tgt_id, rel_type, group))

        return edges

    def _traverse(
        self,
        root_id: str,
        max_depth: int,
    ) -> tuple[list[DependencyPath], set[str]]:
        """Iterative DFS traversal from root_id.

        Returns
        -------
        (paths, cycle_ids)
            paths: all DependencyPath objects discovered.
            cycle_ids: set of entity_ids that would have closed a cycle.
        """
        # Stack entries: (current_entity_id, depth, entity_path, rel_path, edges_so_far)
        # entity_path: includes root and current
        # rel_path: relationship types used at each hop
        # edges_so_far: DependencyEdge for each hop
        stack: list[tuple[str, int, list[str], list[RelationshipType], list[DependencyEdge]]] = [
            (root_id, 0, [root_id], [], [])
        ]

        paths: list[DependencyPath] = []
        seen_path_ids: set[str] = set()
        cycle_ids: set[str] = set()

        while stack:
            current_id, depth, entity_path, rel_path, edges_so_far = stack.pop()

            # Record a path for any non-root node
            if depth > 0:
                pid = make_path_id(entity_path)
                if pid not in seen_path_ids:
                    seen_path_ids.add(pid)
                    path = DependencyPath(
                        path_id=pid,
                        start_entity_id=root_id,
                        end_entity_id=current_id,
                        depth=depth,
                        entity_path=list(entity_path),
                        relationship_path=list(rel_path),
                        edges=list(edges_so_far),
                        is_direct=(depth == 1),
                        is_transitive=(depth > 1),
                    )
                    paths.append(path)

            # Stop descending if at max depth
            if depth >= max_depth:
                continue

            # Get outgoing edges from current node
            outgoing_edges = self._get_outgoing_edges(current_id)

            # Push in REVERSE order so the stack processes them in original order
            # (DFS property: last pushed → first visited)
            for edge in reversed(outgoing_edges):
                neighbor = edge.target_entity_id

                # Skip if neighbor entity doesn't exist (unresolved ref)
                if self._entity_repo.get_by_id(neighbor) is None:
                    logger.debug(
                        "DependencyGraphService: skipping unresolved entity '%s' "
                        "during traversal from '%s'.",
                        neighbor,
                        current_id,
                    )
                    continue

                # Cycle detection: if neighbor is already in the current path
                if neighbor in entity_path:
                    logger.debug(
                        "DependencyGraphService: cycle detected at '%s' in path %s.",
                        neighbor,
                        entity_path,
                    )
                    cycle_ids.add(neighbor)
                    continue

                # Push to stack for further traversal
                stack.append((
                    neighbor,
                    depth + 1,
                    entity_path + [neighbor],
                    rel_path + [edge.relationship_type],
                    edges_so_far + [edge],
                ))

        # Sort paths deterministically: by depth ASC, then entity_path join ASC
        paths.sort(key=lambda p: (p.depth, ":".join(p.entity_path)))

        return paths, cycle_ids
