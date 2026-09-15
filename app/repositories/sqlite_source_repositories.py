"""SQLite adapters for the primary ThreadLine repositories."""

import json
from typing import Callable, Optional

from app.models.dependency import ExplicitDependency
from app.models.entity import CanonicalEntity, EntityMention, EntityType
from app.models.extraction import ExtractionResult
from app.models.meeting import Meeting
from app.models.background_job import BackgroundJob
from app.models.relationships import RelationshipType
from app.persistence.sqlite_store import SQLiteSourceStore
from app.repositories.dependency_repository import AbstractDependencyRepository
from app.repositories.entity_repository import AbstractEntityRepository, _normalize
from app.repositories.extraction_repository import AbstractExtractionRepository
from app.repositories.meeting_repository import AbstractMeetingRepository
from app.repositories.mention_repository import AbstractMentionRepository


def _dump(model) -> str:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True)


def _guard_derived_revision(connection, meeting_id, incoming_revision, *, kind: str) -> None:
    """Enforce exact source-revision equality for derived writes.

    Derived state may only be written against the exact authoritative source
    revision of its meeting.  Both directions are rejected:
      - incoming < current  -> StaleJobOwnershipError (stale worker writing old data)
      - incoming > current  -> FutureRevisionError  (fabricated future revision
                               that would masquerade as current later)
    No authority exists (no meeting row) -> guard is skipped, matching the
    historical behaviour of callers that register derived rows independently.
    """
    row = connection.execute(
        "SELECT source_revision FROM meetings WHERE meeting_id = ?",
        (meeting_id,),
    ).fetchone()
    if row is None:
        return
    current_rev = int(row["source_revision"] or 1)
    incoming = int(incoming_revision or 1)
    if incoming < current_rev:
        from app.repositories.background_job_repository import StaleJobOwnershipError

        raise StaleJobOwnershipError(
            f"stale {kind} write rejected for meeting {meeting_id}: "
            f"incoming revision {incoming} < current source revision {current_rev}"
        )
    if incoming > current_rev:
        from app.services.processing_consistency_service import FutureRevisionError

        raise FutureRevisionError(
            f"future {kind} write rejected for meeting {meeting_id}: "
            f"incoming revision {incoming} > current source revision {current_rev}"
        )


class SQLiteMeetingRepository(AbstractMeetingRepository):
    def __init__(self, store: SQLiteSourceStore) -> None:
        self._store = store

    def save(self, meeting: Meeting) -> None:
        with self._store.transaction() as connection:
            row = connection.execute(
                "SELECT source_revision, payload FROM meetings WHERE meeting_id = ?",
                (meeting.meeting_id,),
            ).fetchone()
            if row is not None:
                current_rev = int(row["source_revision"] or 1)
                if int(meeting.source_revision) < current_rev:
                    from app.repositories.background_job_repository import StaleJobOwnershipError

                    raise StaleJobOwnershipError(
                        f"stale source write rejected for {meeting.meeting_id}: "
                        f"incoming revision {meeting.source_revision} < durable revision {current_rev}"
                    )
                # Compare through the model so rows written before a field
                # existed (defaults applied on read) do not spuriously
                # conflict with the same logical payload.
                if int(meeting.source_revision) == current_rev and _dump(
                    Meeting.model_validate_json(row["payload"])
                ) != _dump(meeting):
                    # Same revision number with different payload: concurrent
                    # refresh lost-update. Caller must re-read and retry as N+1.
                    from app.services.meeting_service import MeetingConflictError

                    raise MeetingConflictError(
                        f"concurrent source refresh for '{meeting.meeting_id}' at revision {current_rev}; retry as revision {current_rev + 1}"
                    )
            connection.execute(
                "INSERT INTO meetings(meeting_id, meeting_date, source_revision, payload, organisation_id) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(meeting_id) DO UPDATE SET meeting_date=excluded.meeting_date, source_revision=excluded.source_revision, payload=excluded.payload, organisation_id=excluded.organisation_id",
                (meeting.meeting_id, meeting.meeting_date.isoformat(), meeting.source_revision, _dump(meeting), meeting.organisation_id),
            )

    def save_and_enqueue(self, meeting: Meeting, job: BackgroundJob) -> None:
        with self._store.transaction() as connection:
            row = connection.execute(
                "SELECT source_revision, payload FROM meetings WHERE meeting_id = ?",
                (meeting.meeting_id,),
            ).fetchone()
            if row is not None:
                current_rev = int(row["source_revision"] or 1)
                if int(meeting.source_revision) < current_rev:
                    from app.repositories.background_job_repository import StaleJobOwnershipError

                    raise StaleJobOwnershipError(
                        f"stale source write rejected for {meeting.meeting_id}: "
                        f"incoming revision {meeting.source_revision} < durable revision {current_rev}"
                    )
                if int(meeting.source_revision) == current_rev and _dump(
                    Meeting.model_validate_json(row["payload"])
                ) != _dump(meeting):
                    from app.services.meeting_service import MeetingConflictError

                    raise MeetingConflictError(
                        f"concurrent source refresh for '{meeting.meeting_id}' at revision {current_rev}; retry as revision {current_rev + 1}"
                    )
            connection.execute(
                "INSERT INTO meetings(meeting_id, meeting_date, source_revision, payload, organisation_id) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(meeting_id) DO UPDATE SET meeting_date=excluded.meeting_date, source_revision=excluded.source_revision, payload=excluded.payload, organisation_id=excluded.organisation_id",
                (meeting.meeting_id, meeting.meeting_date.isoformat(), meeting.source_revision, _dump(meeting), meeting.organisation_id),
            )
            connection.execute(
                """INSERT INTO background_jobs
                (job_id, job_type, payload_id, status, attempts, max_attempts, created_at,
                 started_at, completed_at, last_error, error_type, next_retry_at, lease_until, worker_id, stage, processing_revision, organisation_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO NOTHING""",
                (job.job_id, job.job_type.value, job.payload_id, job.status.value, job.attempts,
                 job.max_attempts, job.created_at.isoformat(), None, None, None, None, None, None, None, job.stage, job.processing_revision, job.organisation_id),
            )

    def get_by_id(self, meeting_id: str) -> Optional[Meeting]:
        row = self._store._connection.execute(
            "SELECT payload FROM meetings WHERE meeting_id = ?", (meeting_id,)
        ).fetchone()
        return Meeting.model_validate_json(row[0]) if row else None

    def list_meetings(
        self,
        organisation_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Meeting]:
        query = "SELECT payload FROM meetings"
        args: list[object] = []
        if organisation_id is not None:
            query += " WHERE organisation_id = ?"
            args.append(organisation_id)
        query += " ORDER BY meeting_date DESC, meeting_id ASC"
        if limit is not None:
            query += " LIMIT ?"
            args.append(max(0, limit))
        rows = self._store._connection.execute(query, tuple(args)).fetchall()
        return [Meeting.model_validate_json(row[0]) for row in rows]


class SQLiteExtractionRepository(AbstractExtractionRepository):
    def __init__(self, store: SQLiteSourceStore) -> None:
        self._store = store

    def save(self, result: ExtractionResult) -> None:
        with self._store.transaction() as connection:
            _guard_derived_revision(connection, result.meeting_id, result.source_revision, kind="extraction")
            connection.execute(
                "INSERT INTO extraction_results(meeting_id, extracted_at, payload, source_revision, organisation_id) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(meeting_id) DO UPDATE SET extracted_at=excluded.extracted_at, payload=excluded.payload, source_revision=excluded.source_revision, organisation_id=excluded.organisation_id",
                (result.meeting_id, result.extracted_at.isoformat(), _dump(result), result.source_revision, result.organisation_id),
            )

    def get_by_meeting_id(self, meeting_id: str) -> Optional[ExtractionResult]:
        row = self._store._connection.execute(
            "SELECT payload FROM extraction_results WHERE meeting_id = ?", (meeting_id,)
        ).fetchone()
        return ExtractionResult.model_validate_json(row[0]) if row else None


class SQLiteEntityRepository(AbstractEntityRepository):
    def __init__(self, store: SQLiteSourceStore) -> None:
        self._store = store

    def create(self, entity: CanonicalEntity) -> None:
        with self._store.transaction() as connection:
            connection.execute(
                "INSERT INTO entities(entity_id, entity_type, canonical_name, payload, organisation_id) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(entity_id) DO UPDATE SET entity_type=excluded.entity_type, canonical_name=excluded.canonical_name, payload=excluded.payload, organisation_id=excluded.organisation_id",
                (entity.entity_id, entity.entity_type.value, entity.canonical_name, _dump(entity), entity.organisation_id),
            )

    def get_by_id(self, entity_id: str) -> Optional[CanonicalEntity]:
        row = self._store._connection.execute(
            "SELECT payload FROM entities WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        return CanonicalEntity.model_validate_json(row[0]) if row else None

    def find_by_canonical_name(self, name: str, entity_type: EntityType, organisation_id: Optional[str] = None) -> Optional[CanonicalEntity]:
        target = _normalize(name)
        if organisation_id is None:
            rows = self._store._connection.execute(
                "SELECT payload FROM entities WHERE entity_type = ? ORDER BY entity_id",
                (entity_type.value,),
            ).fetchall()
        else:
            rows = self._store._connection.execute(
                "SELECT payload FROM entities WHERE entity_type = ? AND organisation_id = ? ORDER BY entity_id",
                (entity_type.value, organisation_id),
            ).fetchall()
        for row in rows:
            entity = CanonicalEntity.model_validate_json(row[0])
            if _normalize(entity.canonical_name) == target or any(
                _normalize(alias) == target for alias in entity.aliases
            ):
                return entity
        return None

    def list_entities(self, entity_type: Optional[EntityType] = None, organisation_id: Optional[str] = None) -> list[CanonicalEntity]:
        clauses = []
        args: list[str] = []
        if entity_type is not None:
            clauses.append("entity_type = ?")
            args.append(entity_type.value)
        if organisation_id is not None:
            clauses.append("organisation_id = ?")
            args.append(organisation_id)
        query = "SELECT payload FROM entities"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY entity_id"
        rows = self._store._connection.execute(query, tuple(args)).fetchall()
        return [CanonicalEntity.model_validate_json(row[0]) for row in rows]

    def add_alias(self, entity_id: str, alias: str) -> Optional[CanonicalEntity]:
        entity = self.get_by_id(entity_id)
        if entity is None:
            return None
        updated = entity.model_copy(update={"aliases": [*entity.aliases, alias]})
        self.create(updated)
        return updated


class SQLiteMentionRepository(AbstractMentionRepository):
    def __init__(self, store: SQLiteSourceStore) -> None:
        self._store = store

    def create(self, mention: EntityMention) -> None:
        with self._store.transaction() as connection:
            _guard_derived_revision(connection, mention.meeting_id, mention.source_revision, kind="mention")
            connection.execute(
                "INSERT INTO entity_mentions(mention_id, meeting_id, entity_id, entity_type, payload, source_revision, organisation_id) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(mention_id) DO UPDATE SET meeting_id=excluded.meeting_id, entity_id=excluded.entity_id, entity_type=excluded.entity_type, payload=excluded.payload, source_revision=excluded.source_revision, organisation_id=excluded.organisation_id",
                (mention.mention_id, mention.meeting_id, mention.entity_id, mention.entity_type.value, _dump(mention), mention.source_revision, mention.organisation_id),
            )

    def get_by_id(self, mention_id: str) -> Optional[EntityMention]:
        row = self._store._connection.execute(
            "SELECT payload FROM entity_mentions WHERE mention_id = ?", (mention_id,)
        ).fetchone()
        return EntityMention.model_validate_json(row[0]) if row else None

    def list_by_meeting_id(self, meeting_id: str, organisation_id: Optional[str] = None) -> list[EntityMention]:
        if organisation_id is None:
            rows = self._store._connection.execute(
                "SELECT payload FROM entity_mentions WHERE meeting_id = ? ORDER BY mention_id", (meeting_id,)
            ).fetchall()
        else:
            rows = self._store._connection.execute(
                "SELECT payload FROM entity_mentions WHERE meeting_id = ? AND organisation_id = ? ORDER BY mention_id",
                (meeting_id, organisation_id),
            ).fetchall()
        return [EntityMention.model_validate_json(row[0]) for row in rows]

    def list_by_entity_id(self, entity_id: str, organisation_id: Optional[str] = None) -> list[EntityMention]:
        if organisation_id is None:
            rows = self._store._connection.execute(
                "SELECT payload FROM entity_mentions WHERE entity_id = ? ORDER BY mention_id", (entity_id,)
            ).fetchall()
        else:
            rows = self._store._connection.execute(
                "SELECT payload FROM entity_mentions WHERE entity_id = ? AND organisation_id = ? ORDER BY mention_id",
                (entity_id, organisation_id),
            ).fetchall()
        return [EntityMention.model_validate_json(row[0]) for row in rows]

    def list_current_by_meeting_id(
        self,
        meeting_id: str,
        current_revision_lookup: Optional[Callable[[str], Optional[int]]] = None,
    ) -> list[EntityMention]:
        from app.repositories.mention_repository import filter_current_mentions

        return filter_current_mentions(self.list_by_meeting_id(meeting_id), current_revision_lookup)

    def list_current_by_entity_id(
        self,
        entity_id: str,
        current_revision_lookup: Optional[Callable[[str], Optional[int]]] = None,
    ) -> list[EntityMention]:
        from app.repositories.mention_repository import filter_current_mentions

        return filter_current_mentions(self.list_by_entity_id(entity_id), current_revision_lookup)

    def update(self, mention: EntityMention) -> None:
        self.create(mention)


class SQLiteDependencyRepository(AbstractDependencyRepository):
    def __init__(self, store: SQLiteSourceStore) -> None:
        self._store = store

    def save(self, dependency: ExplicitDependency) -> None:
        with self._store.transaction() as connection:
            _guard_derived_revision(connection, dependency.meeting_id, dependency.source_revision, kind="dependency")
            connection.execute(
                "INSERT INTO dependencies(dependency_id, source_entity_id, target_entity_id, meeting_id, relationship_type, payload, source_revision, organisation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(dependency_id) DO UPDATE SET source_entity_id=excluded.source_entity_id, target_entity_id=excluded.target_entity_id, meeting_id=excluded.meeting_id, relationship_type=excluded.relationship_type, payload=excluded.payload, source_revision=excluded.source_revision, organisation_id=excluded.organisation_id",
                (dependency.dependency_id, dependency.source_entity_id, dependency.target_entity_id, dependency.meeting_id, dependency.relationship_type.value, _dump(dependency), dependency.source_revision, dependency.organisation_id),
            )

    def get_by_id(self, dependency_id: str) -> Optional[ExplicitDependency]:
        row = self._store._connection.execute(
            "SELECT payload FROM dependencies WHERE dependency_id = ?", (dependency_id,)
        ).fetchone()
        return ExplicitDependency.model_validate_json(row[0]) if row else None

    def list_by_entity_id(self, entity_id: str, organisation_id: Optional[str] = None) -> list[ExplicitDependency]:
        if organisation_id is None:
            clause: str = "source_entity_id = ? OR target_entity_id = ?"
            args: tuple = (entity_id, entity_id)
        else:
            clause = "(source_entity_id = ? OR target_entity_id = ?) AND organisation_id = ?"
            args = (entity_id, entity_id, organisation_id)
        rows = self._store._connection.execute(
            f"SELECT payload FROM dependencies WHERE {clause} ORDER BY dependency_id",
            args,
        ).fetchall()
        return [ExplicitDependency.model_validate_json(row[0]) for row in rows]

    def list_by_source_entity_id(self, source_entity_id: str, organisation_id: Optional[str] = None) -> list[ExplicitDependency]:
        return self._list_where("source_entity_id = ?", (source_entity_id,), organisation_id)

    def list_by_target_entity_id(self, target_entity_id: str, organisation_id: Optional[str] = None) -> list[ExplicitDependency]:
        return self._list_where("target_entity_id = ?", (target_entity_id,), organisation_id)

    def list_by_entity_pair(self, source_entity_id: str, target_entity_id: str, relationship_type: Optional[RelationshipType] = None, organisation_id: Optional[str] = None) -> list[ExplicitDependency]:
        query = "source_entity_id = ? AND target_entity_id = ?"
        args: list[str] = [source_entity_id, target_entity_id]
        if relationship_type is not None:
            query += " AND relationship_type = ?"
            args.append(relationship_type.value)
        return self._list_where(query, tuple(args), organisation_id)

    def _list_where(self, clause: str, args: tuple, organisation_id: Optional[str] = None) -> list[ExplicitDependency]:
        if organisation_id is not None:
            clause = f"({clause}) AND organisation_id = ?"
            args = (*args, organisation_id)
        rows = self._store._connection.execute(
            f"SELECT payload FROM dependencies WHERE {clause} ORDER BY dependency_id", args
        ).fetchall()
        return [ExplicitDependency.model_validate_json(row[0]) for row in rows]

    def list_all(self, organisation_id: Optional[str] = None) -> list[ExplicitDependency]:
        return self._list_where("1 = 1", (), organisation_id)

    def list_current_by_entity_id(
        self,
        entity_id: str,
        current_revision_lookup: Optional[Callable[[str], Optional[int]]] = None,
    ) -> list[ExplicitDependency]:
        from app.repositories.dependency_repository import filter_current_records

        return filter_current_records(self.list_by_entity_id(entity_id), current_revision_lookup)

    def list_current_by_source_entity_id(
        self,
        source_entity_id: str,
        current_revision_lookup: Optional[Callable[[str], Optional[int]]] = None,
    ) -> list[ExplicitDependency]:
        from app.repositories.dependency_repository import filter_current_records

        return filter_current_records(self.list_by_source_entity_id(source_entity_id), current_revision_lookup)

    def list_current_by_target_entity_id(
        self,
        target_entity_id: str,
        current_revision_lookup: Optional[Callable[[str], Optional[int]]] = None,
    ) -> list[ExplicitDependency]:
        from app.repositories.dependency_repository import filter_current_records

        return filter_current_records(self.list_by_target_entity_id(target_entity_id), current_revision_lookup)

    def list_current_all(
        self,
        current_revision_lookup: Optional[Callable[[str], Optional[int]]] = None,
    ) -> list[ExplicitDependency]:
        from app.repositories.dependency_repository import filter_current_records

        return filter_current_records(self.list_all(), current_revision_lookup)
