"""SQLite adapters for the primary ThreadLine repositories."""

import json
from typing import Optional

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


class SQLiteMeetingRepository(AbstractMeetingRepository):
    def __init__(self, store: SQLiteSourceStore) -> None:
        self._store = store

    def save(self, meeting: Meeting) -> None:
        with self._store.transaction() as connection:
            connection.execute(
                "INSERT INTO meetings(meeting_id, meeting_date, source_revision, payload) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(meeting_id) DO UPDATE SET meeting_date=excluded.meeting_date, source_revision=excluded.source_revision, payload=excluded.payload",
                (meeting.meeting_id, meeting.meeting_date.isoformat(), meeting.source_revision, _dump(meeting)),
            )

    def save_and_enqueue(self, meeting: Meeting, job: BackgroundJob) -> None:
        with self._store.transaction() as connection:
            connection.execute(
                "INSERT INTO meetings(meeting_id, meeting_date, source_revision, payload) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(meeting_id) DO UPDATE SET meeting_date=excluded.meeting_date, source_revision=excluded.source_revision, payload=excluded.payload",
                (meeting.meeting_id, meeting.meeting_date.isoformat(), meeting.source_revision, _dump(meeting)),
            )
            connection.execute(
                """INSERT INTO background_jobs
                (job_id, job_type, payload_id, status, attempts, max_attempts, created_at,
                 started_at, completed_at, last_error, error_type, next_retry_at, lease_until, worker_id, stage, processing_revision)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO NOTHING""",
                (job.job_id, job.job_type.value, job.payload_id, job.status.value, job.attempts,
                 job.max_attempts, job.created_at.isoformat(), None, None, None, None, None, None, None, job.stage, job.processing_revision),
            )

    def get_by_id(self, meeting_id: str) -> Optional[Meeting]:
        row = self._store._connection.execute(
            "SELECT payload FROM meetings WHERE meeting_id = ?", (meeting_id,)
        ).fetchone()
        return Meeting.model_validate_json(row[0]) if row else None


class SQLiteExtractionRepository(AbstractExtractionRepository):
    def __init__(self, store: SQLiteSourceStore) -> None:
        self._store = store

    def save(self, result: ExtractionResult) -> None:
        with self._store.transaction() as connection:
            connection.execute(
                "INSERT INTO extraction_results(meeting_id, extracted_at, payload, source_revision) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(meeting_id) DO UPDATE SET extracted_at=excluded.extracted_at, payload=excluded.payload, source_revision=excluded.source_revision",
                (result.meeting_id, result.extracted_at.isoformat(), _dump(result), result.source_revision),
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
                "INSERT INTO entities(entity_id, entity_type, canonical_name, payload) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(entity_id) DO UPDATE SET entity_type=excluded.entity_type, canonical_name=excluded.canonical_name, payload=excluded.payload",
                (entity.entity_id, entity.entity_type.value, entity.canonical_name, _dump(entity)),
            )

    def get_by_id(self, entity_id: str) -> Optional[CanonicalEntity]:
        row = self._store._connection.execute(
            "SELECT payload FROM entities WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        return CanonicalEntity.model_validate_json(row[0]) if row else None

    def find_by_canonical_name(self, name: str, entity_type: EntityType) -> Optional[CanonicalEntity]:
        target = _normalize(name)
        rows = self._store._connection.execute(
            "SELECT payload FROM entities WHERE entity_type = ? ORDER BY entity_id",
            (entity_type.value,),
        ).fetchall()
        for row in rows:
            entity = CanonicalEntity.model_validate_json(row[0])
            if _normalize(entity.canonical_name) == target or any(
                _normalize(alias) == target for alias in entity.aliases
            ):
                return entity
        return None

    def list_entities(self, entity_type: Optional[EntityType] = None) -> list[CanonicalEntity]:
        if entity_type is None:
            rows = self._store._connection.execute(
                "SELECT payload FROM entities ORDER BY entity_id"
            ).fetchall()
        else:
            rows = self._store._connection.execute(
                "SELECT payload FROM entities WHERE entity_type = ? ORDER BY entity_id",
                (entity_type.value,),
            ).fetchall()
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
            connection.execute(
                "INSERT INTO entity_mentions(mention_id, meeting_id, entity_id, entity_type, payload, source_revision) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(mention_id) DO UPDATE SET meeting_id=excluded.meeting_id, entity_id=excluded.entity_id, entity_type=excluded.entity_type, payload=excluded.payload, source_revision=excluded.source_revision",
                (mention.mention_id, mention.meeting_id, mention.entity_id, mention.entity_type.value, _dump(mention), mention.source_revision),
            )

    def get_by_id(self, mention_id: str) -> Optional[EntityMention]:
        row = self._store._connection.execute(
            "SELECT payload FROM entity_mentions WHERE mention_id = ?", (mention_id,)
        ).fetchone()
        return EntityMention.model_validate_json(row[0]) if row else None

    def list_by_meeting_id(self, meeting_id: str) -> list[EntityMention]:
        rows = self._store._connection.execute(
            "SELECT payload FROM entity_mentions WHERE meeting_id = ? ORDER BY mention_id", (meeting_id,)
        ).fetchall()
        return [EntityMention.model_validate_json(row[0]) for row in rows]

    def list_by_entity_id(self, entity_id: str) -> list[EntityMention]:
        rows = self._store._connection.execute(
            "SELECT payload FROM entity_mentions WHERE entity_id = ? ORDER BY mention_id", (entity_id,)
        ).fetchall()
        return [EntityMention.model_validate_json(row[0]) for row in rows]

    def update(self, mention: EntityMention) -> None:
        self.create(mention)


class SQLiteDependencyRepository(AbstractDependencyRepository):
    def __init__(self, store: SQLiteSourceStore) -> None:
        self._store = store

    def save(self, dependency: ExplicitDependency) -> None:
        with self._store.transaction() as connection:
            connection.execute(
                "INSERT INTO dependencies(dependency_id, source_entity_id, target_entity_id, meeting_id, relationship_type, payload, source_revision) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(dependency_id) DO UPDATE SET source_entity_id=excluded.source_entity_id, target_entity_id=excluded.target_entity_id, meeting_id=excluded.meeting_id, relationship_type=excluded.relationship_type, payload=excluded.payload, source_revision=excluded.source_revision",
                (dependency.dependency_id, dependency.source_entity_id, dependency.target_entity_id, dependency.meeting_id, dependency.relationship_type.value, _dump(dependency), dependency.source_revision),
            )

    def get_by_id(self, dependency_id: str) -> Optional[ExplicitDependency]:
        row = self._store._connection.execute(
            "SELECT payload FROM dependencies WHERE dependency_id = ?", (dependency_id,)
        ).fetchone()
        return ExplicitDependency.model_validate_json(row[0]) if row else None

    def list_by_entity_id(self, entity_id: str) -> list[ExplicitDependency]:
        rows = self._store._connection.execute(
            "SELECT payload FROM dependencies WHERE source_entity_id = ? OR target_entity_id = ? ORDER BY dependency_id",
            (entity_id, entity_id),
        ).fetchall()
        return [ExplicitDependency.model_validate_json(row[0]) for row in rows]

    def list_by_source_entity_id(self, source_entity_id: str) -> list[ExplicitDependency]:
        return self._list_where("source_entity_id = ?", (source_entity_id,))

    def list_by_target_entity_id(self, target_entity_id: str) -> list[ExplicitDependency]:
        return self._list_where("target_entity_id = ?", (target_entity_id,))

    def list_by_entity_pair(self, source_entity_id: str, target_entity_id: str, relationship_type: Optional[RelationshipType] = None) -> list[ExplicitDependency]:
        query = "source_entity_id = ? AND target_entity_id = ?"
        args: list[str] = [source_entity_id, target_entity_id]
        if relationship_type is not None:
            query += " AND relationship_type = ?"
            args.append(relationship_type.value)
        return self._list_where(query, tuple(args))

    def _list_where(self, clause: str, args: tuple) -> list[ExplicitDependency]:
        rows = self._store._connection.execute(
            f"SELECT payload FROM dependencies WHERE {clause} ORDER BY dependency_id", args
        ).fetchall()
        return [ExplicitDependency.model_validate_json(row[0]) for row in rows]

    def list_all(self) -> list[ExplicitDependency]:
        return self._list_where("1 = 1", ())
