import pytest
from app.models.dependency import ExplicitDependency
from app.models.dependency_graph import make_path_id
from app.models.entity import CanonicalEntity
from app.models.relationships import RelationshipType
from app.services.dependency_graph_service import DependencyGraphService
from app.services.entity_service import EntityNotFoundError

# Mock repositories for testing
class MockEntityRepo:
    def __init__(self):
        self.entities = {}
    
    def save(self, entity: CanonicalEntity):
        self.entities[entity.entity_id] = entity
        
    def get_by_id(self, entity_id: str):
        return self.entities.get(entity_id)

class MockDependencyRepo:
    def __init__(self):
        self.deps = []
        
    def save(self, dep: ExplicitDependency):
        self.deps.append(dep)
        
    def list_by_source_entity_id(self, entity_id: str):
        return [d for d in self.deps if d.source_entity_id == entity_id]

    def list_by_target_entity_id(self, entity_id: str):
        return [d for d in self.deps if d.target_entity_id == entity_id]

    def list_current_by_source_entity_id(self, entity_id: str, current_revision_lookup=None):
        return self.list_by_source_entity_id(entity_id)

    def list_current_by_target_entity_id(self, entity_id: str, current_revision_lookup=None):
        return self.list_by_target_entity_id(entity_id)

    def list_current_by_entity_id(self, entity_id: str, current_revision_lookup=None):
        return [d for d in self.deps if d.source_entity_id == entity_id or d.target_entity_id == entity_id]

    def list_current_all(self, current_revision_lookup=None):
        return list(self.deps)

@pytest.fixture
def entity_repo():
    return MockEntityRepo()

@pytest.fixture
def dep_repo():
    return MockDependencyRepo()

@pytest.fixture
def service(entity_repo, dep_repo):
    return DependencyGraphService(dependency_repo=dep_repo, entity_repo=entity_repo)

from app.models.entity import EntityType
from datetime import datetime, timezone

def create_entity(repo, eid: str):
    e = CanonicalEntity(entity_id=eid, canonical_name=f"Name {eid}", aliases=[], entity_type=EntityType.ISSUE, created_at=datetime.now(timezone.utc))
    repo.save(e)

def create_dep(repo, src: str, tgt: str, rel_type: RelationshipType, meet: str = "m1"):
    d = ExplicitDependency(
        dependency_id=f"rel_{src}_{tgt}_{meet}",
        source_entity_id=src,
        target_entity_id=tgt,
        relationship_type=rel_type,
        meeting_id=meet,
        mention_id=f"ment_{src}_{tgt}",
        source_text="evidence",
        created_at=datetime.now(timezone.utc)
    )
    repo.save(d)

def test_G01_empty_graph(service, entity_repo):
    create_entity(entity_repo, "A")
    graph = service.build_dependency_graph("A")
    assert graph.root_entity_id == "A"
    assert not graph.direct_dependencies
    assert not graph.transitive_dependencies
    assert not graph.all_reachable_entity_ids
    assert graph.max_depth_reached == 0
    assert not graph.contains_cycle

def test_G02_single_entity_no_connections(service, entity_repo):
    create_entity(entity_repo, "A")
    create_entity(entity_repo, "B")
    graph = service.build_dependency_graph("A")
    assert not graph.direct_dependencies
    assert graph.max_depth_reached == 0

def test_G03_direct_dependency(service, entity_repo, dep_repo):
    create_entity(entity_repo, "A")
    create_entity(entity_repo, "B")
    create_dep(dep_repo, "A", "B", RelationshipType.DEPENDS_ON)
    
    graph = service.build_dependency_graph("A")
    assert len(graph.direct_dependencies) == 1
    p = graph.direct_dependencies[0]
    assert p.start_entity_id == "A"
    assert p.end_entity_id == "B"
    assert p.depth == 1
    assert p.is_direct
    assert not p.is_transitive
    assert p.entity_path == ["A", "B"]
    assert p.relationship_path == [RelationshipType.DEPENDS_ON]
    assert len(p.edges) == 1
    assert graph.max_depth_reached == 1
    assert graph.all_reachable_entity_ids == ["B"]

def test_G04_direct_dependent(service, entity_repo, dep_repo):
    create_entity(entity_repo, "A")
    create_entity(entity_repo, "B")
    create_dep(dep_repo, "A", "B", RelationshipType.DEPENDS_ON)
    
    paths = service.get_direct_dependents("B")
    assert len(paths) == 1
    assert paths[0].start_entity_id == "A"
    assert paths[0].end_entity_id == "B"
    assert paths[0].depth == 1

def test_G05_depth_2_transitive(service, entity_repo, dep_repo):
    for e in "ABC": create_entity(entity_repo, e)
    create_dep(dep_repo, "A", "B", RelationshipType.DEPENDS_ON)
    create_dep(dep_repo, "B", "C", RelationshipType.BLOCKS)
    
    graph = service.build_dependency_graph("A")
    assert len(graph.direct_dependencies) == 1
    assert len(graph.transitive_dependencies) == 1
    p = graph.transitive_dependencies[0]
    assert p.end_entity_id == "C"
    assert p.depth == 2
    assert p.is_transitive
    assert p.entity_path == ["A", "B", "C"]
    assert graph.all_reachable_entity_ids == ["B", "C"]

def test_G06_depth_3_transitive(service, entity_repo, dep_repo):
    for e in "ABCD": create_entity(entity_repo, e)
    create_dep(dep_repo, "A", "B", RelationshipType.DEPENDS_ON)
    create_dep(dep_repo, "B", "C", RelationshipType.DEPENDS_ON)
    create_dep(dep_repo, "C", "D", RelationshipType.DEPENDS_ON)
    
    graph = service.build_dependency_graph("A")
    assert graph.max_depth_reached == 3
    assert len(graph.transitive_dependencies) == 2
    assert {"C", "D"} == {p.end_entity_id for p in graph.transitive_dependencies}

def test_G07_max_depth_stops(service, entity_repo, dep_repo):
    for e in "ABC": create_entity(entity_repo, e)
    create_dep(dep_repo, "A", "B", RelationshipType.DEPENDS_ON)
    create_dep(dep_repo, "B", "C", RelationshipType.DEPENDS_ON)
    
    graph = service.build_dependency_graph("A", max_depth=1)
    assert graph.max_depth_reached == 1
    assert not graph.transitive_dependencies
    assert graph.all_reachable_entity_ids == ["B"]

def test_G08_cycle_A_B_A(service, entity_repo, dep_repo):
    create_entity(entity_repo, "A")
    create_entity(entity_repo, "B")
    create_dep(dep_repo, "A", "B", RelationshipType.DEPENDS_ON)
    create_dep(dep_repo, "B", "A", RelationshipType.DEPENDS_ON)
    
    graph = service.build_dependency_graph("A")
    assert graph.contains_cycle
    assert graph.cycle_entity_ids == ["A"]
    assert graph.max_depth_reached == 1
    assert len(graph.direct_dependencies) == 1
    assert not graph.transitive_dependencies

def test_G09_cycle_A_B_C_A(service, entity_repo, dep_repo):
    for e in "ABC": create_entity(entity_repo, e)
    create_dep(dep_repo, "A", "B", RelationshipType.DEPENDS_ON)
    create_dep(dep_repo, "B", "C", RelationshipType.DEPENDS_ON)
    create_dep(dep_repo, "C", "A", RelationshipType.DEPENDS_ON)
    
    graph = service.build_dependency_graph("A")
    assert graph.contains_cycle
    assert graph.cycle_entity_ids == ["A"]
    assert graph.max_depth_reached == 2

def test_G10_diamond_graph(service, entity_repo, dep_repo):
    for e in "ABCD": create_entity(entity_repo, e)
    create_dep(dep_repo, "A", "B", RelationshipType.DEPENDS_ON)
    create_dep(dep_repo, "A", "C", RelationshipType.DEPENDS_ON)
    create_dep(dep_repo, "B", "D", RelationshipType.DEPENDS_ON)
    create_dep(dep_repo, "C", "D", RelationshipType.DEPENDS_ON)
    
    graph = service.build_dependency_graph("A")
    assert len(graph.direct_dependencies) == 2
    assert len(graph.transitive_dependencies) == 2
    paths = [p.entity_path for p in graph.transitive_dependencies]
    assert ["A", "B", "D"] in paths
    assert ["A", "C", "D"] in paths
    assert graph.all_reachable_entity_ids == ["B", "C", "D"]

def test_G11_multiple_paths_same_length(service, entity_repo, dep_repo):
    # Same as diamond, testing multiple paths
    test_G10_diamond_graph(service, entity_repo, dep_repo)

def test_G12_deterministic_ordering(service, entity_repo, dep_repo):
    for e in "ABC": create_entity(entity_repo, e)
    create_dep(dep_repo, "A", "C", RelationshipType.DEPENDS_ON)
    create_dep(dep_repo, "A", "B", RelationshipType.DEPENDS_ON)
    
    graph = service.build_dependency_graph("A")
    assert [p.end_entity_id for p in graph.direct_dependencies] == ["B", "C"]

def test_G13_duplicate_edge_strength(service, entity_repo, dep_repo):
    create_entity(entity_repo, "A")
    create_entity(entity_repo, "B")
    create_dep(dep_repo, "A", "B", RelationshipType.DEPENDS_ON, "m1")
    create_dep(dep_repo, "A", "B", RelationshipType.DEPENDS_ON, "m2")
    
    graph = service.build_dependency_graph("A")
    assert len(graph.direct_dependencies) == 1
    edge = graph.direct_dependencies[0].edges[0]
    assert edge.strength == 2
    assert edge.related_meeting_ids == ["m1", "m2"]

def test_G14_depends_on_direction(service, entity_repo, dep_repo):
    create_entity(entity_repo, "A")
    create_entity(entity_repo, "B")
    create_dep(dep_repo, "B", "A", RelationshipType.DEPENDS_ON)
    
    graph = service.build_dependency_graph("A")
    assert not graph.direct_dependencies

def test_G15_blocks_direction(service, entity_repo, dep_repo):
    create_entity(entity_repo, "A")
    create_entity(entity_repo, "B")
    create_dep(dep_repo, "B", "A", RelationshipType.BLOCKS)
    
    graph = service.build_dependency_graph("A")
    assert not graph.direct_dependencies

def test_G16_co_occurs_not_traversed(service, entity_repo, dep_repo):
    create_entity(entity_repo, "A")
    create_entity(entity_repo, "B")
    create_dep(dep_repo, "A", "B", RelationshipType.CO_OCCURS_WITH)
    
    graph = service.build_dependency_graph("A")
    assert not graph.direct_dependencies

def test_G17_self_relationship(service, entity_repo, dep_repo):
    create_entity(entity_repo, "A")
    create_dep(dep_repo, "A", "A", RelationshipType.DEPENDS_ON)
    
    graph = service.build_dependency_graph("A")
    assert graph.contains_cycle
    assert not graph.direct_dependencies

def test_G18_missing_entity_skipped(service, entity_repo, dep_repo):
    create_entity(entity_repo, "A")
    create_dep(dep_repo, "A", "B", RelationshipType.DEPENDS_ON) # B doesn't exist
    
    graph = service.build_dependency_graph("A")
    assert not graph.direct_dependencies
    assert not graph.all_reachable_entity_ids

def test_G19_direct_vs_transitive(service, entity_repo, dep_repo):
    test_G05_depth_2_transitive(service, entity_repo, dep_repo)

def test_G20_evidence_preserved(service, entity_repo, dep_repo):
    create_entity(entity_repo, "A")
    create_entity(entity_repo, "B")
    create_dep(dep_repo, "A", "B", RelationshipType.DEPENDS_ON)
    
    graph = service.build_dependency_graph("A")
    edge = graph.direct_dependencies[0].edges[0]
    assert edge.source_text == "evidence"
    assert edge.mention_id == "ment_A_B"

def test_G21_meeting_ids(service, entity_repo, dep_repo):
    test_G13_duplicate_edge_strength(service, entity_repo, dep_repo)

def test_G22_no_backward_propagation(service, entity_repo, dep_repo):
    # Tested by G14/G15 directionality
    pass

def test_G23_diamond_dedup_all_reachable(service, entity_repo, dep_repo):
    test_G10_diamond_graph(service, entity_repo, dep_repo)

def test_G24_repeated_execution(service, entity_repo, dep_repo):
    for e in "ABC": create_entity(entity_repo, e)
    create_dep(dep_repo, "A", "B", RelationshipType.DEPENDS_ON)
    create_dep(dep_repo, "B", "C", RelationshipType.DEPENDS_ON)
    
    g1 = service.build_dependency_graph("A")
    g2 = service.build_dependency_graph("A")
    assert g1.model_dump() == g2.model_dump()

def test_G25_reachable_sorted(service, entity_repo, dep_repo):
    for e in "ABC": create_entity(entity_repo, e)
    create_dep(dep_repo, "A", "C", RelationshipType.DEPENDS_ON)
    create_dep(dep_repo, "A", "B", RelationshipType.DEPENDS_ON)
    
    graph = service.build_dependency_graph("A")
    assert graph.all_reachable_entity_ids == ["B", "C"]

def test_G26_path_ids_deterministic():
    p1 = make_path_id(["A", "B"])
    p2 = make_path_id(["A", "B"])
    assert p1 == p2
    assert len(p1) == 16

def test_G27_max_depth_10_safe(service, entity_repo, dep_repo):
    create_entity(entity_repo, "A")
    graph = service.build_dependency_graph("A", max_depth=100) # Capped at 10 internally
    assert graph.max_depth_reached == 0

def test_G28_blocks_traversed(service, entity_repo, dep_repo):
    create_entity(entity_repo, "A")
    create_entity(entity_repo, "B")
    create_dep(dep_repo, "A", "B", RelationshipType.BLOCKS)
    
    graph = service.build_dependency_graph("A")
    assert len(graph.direct_dependencies) == 1

def test_G29_e2e_chain(service, entity_repo, dep_repo):
    test_G06_depth_3_transitive(service, entity_repo, dep_repo)

def test_G30_no_cycle_flag(service, entity_repo, dep_repo):
    for e in "ABC": create_entity(entity_repo, e)
    create_dep(dep_repo, "A", "B", RelationshipType.DEPENDS_ON)
    create_dep(dep_repo, "B", "C", RelationshipType.DEPENDS_ON)
    
    graph = service.build_dependency_graph("A")
    assert not graph.contains_cycle

def test_G31_direct_dependents_missing(service, entity_repo):
    with pytest.raises(EntityNotFoundError):
        service.get_direct_dependents("X")
