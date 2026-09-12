"""Repository abstraction for semantic index records (Stage 20).

Provides CRUD interface for persistent semantic index records.
Implementations may be in-memory, file-based, or database-backed.

Pattern matches existing repository conventions in ThreadLine.
"""

from abc import ABC, abstractmethod
import json
import os
from pathlib import Path
from threading import RLock
from typing import Optional

from app.models.semantic_index import SemanticIndexRecord


class AbstractSemanticIndexRepository(ABC):
    """Repository for semantic index records.

    Provides persistent storage and retrieval of embedding vectors.
    Implementations must ensure:
      - Composite identity safety (evidence_id + model + version)
      - No duplicate records by composite key
      - Deterministic ordering
      - Read-only for organisational state (embeddings are derived data)
    """

    @abstractmethod
    def get_by_composite_key(
        self,
        evidence_id: str,
        embedding_model: str,
        representation_version: str,
    ) -> Optional[SemanticIndexRecord]:
        """Retrieve record by composite key.

        Parameters
        ----------
        evidence_id:
            The evidence identifier.
        embedding_model:
            The embedding model name.
        representation_version:
            The representation version.

        Returns
        -------
        Optional[SemanticIndexRecord]
            The record, or None if not found.
        """
        ...

    @abstractmethod
    def get_by_evidence_id(self, evidence_id: str) -> list[SemanticIndexRecord]:
        """Retrieve all records for a given evidence_id.

        Returns all embeddings across different models/versions.

        Parameters
        ----------
        evidence_id:
            The evidence identifier.

        Returns
        -------
        list[SemanticIndexRecord]
            Records for this evidence_id, or empty list.
        """
        ...

    @abstractmethod
    def upsert(self, record: SemanticIndexRecord) -> SemanticIndexRecord:
        """Insert or update a record.

        If a record with the same composite key exists, update it.
        Otherwise, insert a new record.

        Parameters
        ----------
        record:
            The record to insert or update.

        Returns
        -------
        SemanticIndexRecord
            The persisted record (may have been updated).
        """
        ...

    @abstractmethod
    def delete(
        self,
        evidence_id: str,
        embedding_model: str,
        representation_version: str,
    ) -> bool:
        """Delete a record by composite key.

        Parameters
        ----------
        evidence_id, embedding_model, representation_version:
            Composite key to identify the record.

        Returns
        -------
        bool
            True if a record was deleted, False if not found.
        """
        ...

    @abstractmethod
    def delete_by_evidence_id(self, evidence_id: str) -> int:
        """Delete all records for a given evidence_id.

        Parameters
        ----------
        evidence_id:
            The evidence identifier.

        Returns
        -------
        int
            Number of records deleted.
        """
        ...

    @abstractmethod
    def exists(
        self,
        evidence_id: str,
        embedding_model: str,
        representation_version: str,
    ) -> bool:
        """Check if a record exists by composite key.

        Parameters
        ----------
        evidence_id, embedding_model, representation_version:
            Composite key.

        Returns
        -------
        bool
            True if record exists.
        """
        ...

    @abstractmethod
    def list_by_model(self, embedding_model: str) -> list[SemanticIndexRecord]:
        """Retrieve all records for a given embedding model.

        Parameters
        ----------
        embedding_model:
            The model name.

        Returns
        -------
        list[SemanticIndexRecord]
            Records, sorted deterministically.
        """
        ...

    @abstractmethod
    def list_all(self) -> list[SemanticIndexRecord]:
        """Retrieve all records.

        Returns
        -------
        list[SemanticIndexRecord]
            All records, sorted deterministically.
        """
        ...

    @abstractmethod
    def count(self) -> int:
        """Count total records in the repository.

        Returns
        -------
        int
            Record count.
        """
        ...

    def count_by_model(self, embedding_model: str) -> int:
        """Count records for a given model.

        Default implementation; may be overridden for efficiency.

        Parameters
        ----------
        embedding_model:
            The model name.

        Returns
        -------
        int
            Record count.
        """
        return len(self.list_by_model(embedding_model))


# ---------------------------------------------------------------------------
# In-Memory Implementation
# ---------------------------------------------------------------------------

class InMemorySemanticIndexRepository(AbstractSemanticIndexRepository):
    """In-memory semantic index repository for testing and development.

    Suitable for testing and single-process scenarios.
    Not suitable for production multi-process deployments without additional locking.
    """

    def __init__(self) -> None:
        """Initialize an empty in-memory repository."""
        # Key: (evidence_id, embedding_model, representation_version)
        self._records: dict[tuple[str, str, str], SemanticIndexRecord] = {}

    def get_by_composite_key(
        self,
        evidence_id: str,
        embedding_model: str,
        representation_version: str,
    ) -> Optional[SemanticIndexRecord]:
        """Retrieve record by composite key."""
        key = (evidence_id, embedding_model, representation_version)
        return self._records.get(key)

    def get_by_evidence_id(self, evidence_id: str) -> list[SemanticIndexRecord]:
        """Retrieve all records for a given evidence_id."""
        results = [
            record
            for key, record in self._records.items()
            if key[0] == evidence_id
        ]
        # Sort deterministically by (model, version, evidence_id)
        return sorted(
            results,
            key=lambda r: (r.embedding_model, r.representation_version, r.evidence_id),
        )

    def upsert(self, record: SemanticIndexRecord) -> SemanticIndexRecord:
        """Insert or update a record."""
        key = (
            record.evidence_id,
            record.embedding_model,
            record.representation_version,
        )
        self._records[key] = record
        return record

    def delete(
        self,
        evidence_id: str,
        embedding_model: str,
        representation_version: str,
    ) -> bool:
        """Delete a record by composite key."""
        key = (evidence_id, embedding_model, representation_version)
        if key in self._records:
            del self._records[key]
            return True
        return False

    def delete_by_evidence_id(self, evidence_id: str) -> int:
        """Delete all records for a given evidence_id."""
        keys_to_delete = [
            key for key in self._records.keys() if key[0] == evidence_id
        ]
        for key in keys_to_delete:
            del self._records[key]
        return len(keys_to_delete)

    def exists(
        self,
        evidence_id: str,
        embedding_model: str,
        representation_version: str,
    ) -> bool:
        """Check if a record exists by composite key."""
        key = (evidence_id, embedding_model, representation_version)
        return key in self._records

    def list_by_model(self, embedding_model: str) -> list[SemanticIndexRecord]:
        """Retrieve all records for a given embedding model."""
        results = [
            record
            for record in self._records.values()
            if record.embedding_model == embedding_model
        ]
        # Sort deterministically
        return sorted(results, key=lambda r: (r.evidence_id, r.representation_version))

    def list_all(self) -> list[SemanticIndexRecord]:
        """Retrieve all records."""
        results = list(self._records.values())
        # Sort deterministically
        return sorted(
            results, key=lambda r: (r.embedding_model, r.evidence_id, r.representation_version)
        )

    def count(self) -> int:
        """Count total records."""
        return len(self._records)


# ---------------------------------------------------------------------------
# JSON File Implementation
# ---------------------------------------------------------------------------

class JsonFileSemanticIndexRepository(AbstractSemanticIndexRepository):
    """Durable semantic index backed by an atomically replaced JSON file.

    ThreadLine has no database or ORM layer yet. This adapter gives the
    derived semantic index durability without introducing a second, partial
    source-of-truth database architecture. Writes are serialized in-process
    and committed through ``os.replace`` so readers never observe a partial
    JSON document.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = RLock()
        self._records: dict[tuple[str, str, str], SemanticIndexRecord] = {}
        self._load()

    @staticmethod
    def _key(record: SemanticIndexRecord) -> tuple[str, str, str]:
        return (
            record.evidence_id,
            record.embedding_model,
            record.representation_version,
        )

    def _load(self) -> None:
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, list):
            raise ValueError("semantic index file must contain a JSON list")
        for item in payload:
            record = SemanticIndexRecord.model_validate(item)
            self._records[self._key(record)] = record

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = [
            record.model_dump(mode="json")
            for record in sorted(
                self._records.values(),
                key=lambda item: (
                    item.embedding_model,
                    item.evidence_id,
                    item.representation_version,
                ),
            )
        ]
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, self._path)

    def get_by_composite_key(
        self,
        evidence_id: str,
        embedding_model: str,
        representation_version: str,
    ) -> Optional[SemanticIndexRecord]:
        with self._lock:
            return self._records.get((evidence_id, embedding_model, representation_version))

    def get_by_evidence_id(self, evidence_id: str) -> list[SemanticIndexRecord]:
        with self._lock:
            records = [record for record in self._records.values() if record.evidence_id == evidence_id]
            return sorted(records, key=lambda item: (item.embedding_model, item.representation_version))

    def upsert(self, record: SemanticIndexRecord) -> SemanticIndexRecord:
        with self._lock:
            self._records[self._key(record)] = record
            self._persist()
            return record

    def delete(
        self,
        evidence_id: str,
        embedding_model: str,
        representation_version: str,
    ) -> bool:
        with self._lock:
            key = (evidence_id, embedding_model, representation_version)
            if key not in self._records:
                return False
            del self._records[key]
            self._persist()
            return True

    def delete_by_evidence_id(self, evidence_id: str) -> int:
        with self._lock:
            keys = [key for key in self._records if key[0] == evidence_id]
            for key in keys:
                del self._records[key]
            if keys:
                self._persist()
            return len(keys)

    def exists(
        self,
        evidence_id: str,
        embedding_model: str,
        representation_version: str,
    ) -> bool:
        with self._lock:
            return (evidence_id, embedding_model, representation_version) in self._records

    def list_by_model(self, embedding_model: str) -> list[SemanticIndexRecord]:
        with self._lock:
            records = [record for record in self._records.values() if record.embedding_model == embedding_model]
            return sorted(records, key=lambda item: (item.evidence_id, item.representation_version))

    def list_all(self) -> list[SemanticIndexRecord]:
        with self._lock:
            return sorted(
                self._records.values(),
                key=lambda item: (item.embedding_model, item.evidence_id, item.representation_version),
            )

    def count(self) -> int:
        with self._lock:
            return len(self._records)
