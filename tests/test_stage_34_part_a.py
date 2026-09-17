"""Stage 34 Part A: dead-code removal and annotation regression guards.

A1: app/services/evidence_index_service.py was deleted (unused, untested,
superseded by the Stage 21 semantic-index architecture).  These tests guard
against reintroduction and against stale references.

A2: app/services/semantic_indexing_service.py referenced ``Callable`` in a
string annotation without importing it.  The import is fixed; these tests
prove the annotation resolves at runtime via ``typing.get_type_hints``.
"""

import importlib.util
import typing


class TestDeadCodeRemoval:
    def test_evidence_index_service_module_is_gone(self):
        assert (
            importlib.util.find_spec("app.services.evidence_index_service")
            is None
        ), "evidence_index_service.py must stay deleted (Stage 34 A1)"

    def test_no_live_reference_to_evidence_index_service(self):
        import pathlib

        repo = pathlib.Path(__file__).resolve().parent.parent
        offenders = []
        scanned = list((repo / "app").rglob("*.py")) + [
            p
            for p in (repo / "tests").rglob("*.py")
            # This guard file necessarily names the removed module.
            if p.name != "test_stage_34_part_a.py"
        ]
        for path in scanned:
            text = path.read_text(encoding="utf-8")
            if "evidence_index_service" in text or "EvidenceIndexService" in text:
                offenders.append(str(path.relative_to(repo)))
        assert offenders == [], f"stale references remain: {offenders}"


class TestCallableAnnotationResolution:
    def test_init_hints_resolve_without_name_error(self):
        from app.services.semantic_indexing_service import (
            SemanticIndexingService,
        )

        hints = typing.get_type_hints(SemanticIndexingService.__init__)
        assert "current_revision_lookup" in hints

    def test_lookup_annotation_is_optional_callable(self):
        from app.services.semantic_indexing_service import (
            SemanticIndexingService,
        )

        hints = typing.get_type_hints(SemanticIndexingService.__init__)
        annotation = hints["current_revision_lookup"]
        # Optional[Callable[[str | None], int | None]]
        args = typing.get_args(annotation)
        assert len(args) == 2 and type(None) in args
        non_none = args[0] if args[1] is type(None) else args[1]
        assert typing.get_origin(non_none) is not None or callable(non_none)

    def test_callable_symbol_importable_from_module_namespace(self):
        import app.services.semantic_indexing_service as module

        assert module.Callable is typing.Callable

    def test_service_still_constructs_with_lookup(self):
        from app.providers.fake_embedding_provider import FakeEmbeddingProvider
        from app.services.semantic_indexing_service import (
            SemanticIndexingService,
        )

        class _Repo:
            pass

        seen = []

        def lookup(meeting_id):
            seen.append(meeting_id)
            return 3

        svc = SemanticIndexingService(
            embedding_provider=FakeEmbeddingProvider(),
            repository=_Repo(),
            current_revision_lookup=lookup,
        )
        assert svc._current_revision_lookup("m-1") == 3
        assert seen == ["m-1"]

    def test_service_still_constructs_without_lookup(self):
        from app.providers.fake_embedding_provider import FakeEmbeddingProvider
        from app.services.semantic_indexing_service import (
            SemanticIndexingService,
        )

        class _Repo:
            pass

        svc = SemanticIndexingService(
            embedding_provider=FakeEmbeddingProvider(), repository=_Repo()
        )
        assert svc._current_revision_lookup is None
