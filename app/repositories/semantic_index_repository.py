"""Repository abstraction for semantic index records (Stage 20).

Provides CRUD interface for persistent semantic index records.
Implementations may be in-memory, file-based, or database-backed.

Pattern matches existing repository conventions in ThreadLine.
"""

from abc import ABC, abstractmethod
import json
import logging
import os
import sys
from pathlib import Path
from threading import RLock
from typing import Optional

from app.models.semantic_index import SemanticIndexRecord

logger = logging.getLogger(__name__)


def _similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    return max(0.0, min(1.0, sum(a * b for a, b in zip(vec_a, vec_b))))


# ---------------------------------------------------------------------------
# OS-level file locking for cross-process JSON index safety
# ---------------------------------------------------------------------------

class _FileLock:
    """Cross-platform OS-level file lock for multi-process semantic index safety.

    Uses ``msvcrt`` on Windows and ``fcntl`` on Unix.  The lock is held on a
    sidecar ``<path>.lock`` file and released on unlock or file-descriptor close
    (including unexpected process termination).  When OS-level locking is
    unavailable (platform missing ``msvcrt``/``fcntl``), the context manager
    silently falls back to no-op so callers can still rely on in-process
    ``threading.RLock`` for single-process thread safety.

    Thread safety: ONE lock instance is shared by every thread using a
    repository singleton (API threads + worker thread).  An internal guard
    plus a held-count makes concurrent ``with`` blocks mutually exclusive
    and same-thread nesting safe, so two threads can never clobber each
    other's file descriptor (which previously made one thread unlock/close
    the other's descriptor → PermissionError on Windows).

    Usage::

        with _FileLock(lock_path):
            # protected region — OS-level exclusive lock held
    """

    def __init__(self, lock_path: str | Path) -> None:
        self._path = Path(lock_path)
        self._guard = RLock()
        self._count = 0
        self._fd = None

    def __enter__(self) -> None:
        self._guard.acquire()
        try:
            if self._count > 0:
                # Same thread re-entering: the OS lock is already held.
                self._count += 1
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._fd = self._path.open("a+b")
            except OSError as exc:
                logger.warning(
                    "OS-level file locking unavailable (could not open %s): %s",
                    self._path,
                    exc,
                )
                self._fd = None
                self._count += 1
                return
            try:
                if sys.platform == "win32":
                    import msvcrt
                    # msvcrt.locking only locks bytes that already exist within
                    # the file; seed a single byte so an empty lock file works.
                    self._fd.seek(0, 2)
                    if self._fd.tell() == 0:
                        self._fd.write(b"\x00")
                        self._fd.flush()
                    self._fd.seek(0, 0)
                    msvcrt.locking(self._fd.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError, PermissionError) as exc:
                logger.warning(
                    "OS-level file locking unavailable for %s: %s",
                    self._path,
                    exc,
                )
                if self._fd is not None:
                    self._fd.close()
                    self._fd = None
            self._count += 1
        except BaseException:
            self._guard.release()
            raise

    def __exit__(self, *exc_info) -> None:
        try:
            self._count -= 1
            if self._count > 0 or self._fd is None:
                return
            try:
                if sys.platform == "win32":
                    import msvcrt
                    self._fd.seek(0, 0)
                    msvcrt.locking(self._fd.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
            finally:
                self._fd.close()
                self._fd = None
        finally:
            self._guard.release()


class AbstractSemanticIndexRepository(ABC):
    """Repository for semantic index records.

    Provides persistent storage and retrieval of embedding vectors.
    Implementations must ensure:
      - Composite identity safety (organisation_id + evidence_id + model + version)
      - No duplicate records by composite key
      - Deterministic ordering
      - Read-only for organisational state (embeddings are derived data)

    Every read method accepts an optional organisation_id tenant filter.
    None preserves the legacy unscoped behaviour (used by maintenance paths
    and pre-tenant tests).  Production query paths MUST pass an explicit
    organisation_id — a vector search without tenant scope is a
    cross-tenant leak.
    """

    @abstractmethod
    def get_by_composite_key(
        self,
        evidence_id: str,
        embedding_model: str,
        representation_version: str,
        organisation_id: Optional[str] = None,
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
    def get_by_evidence_id(
        self, evidence_id: str, organisation_id: Optional[str] = None
    ) -> list[SemanticIndexRecord]:
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
        organisation_id: Optional[str] = None,
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
    def delete_by_evidence_id(
        self, evidence_id: str, organisation_id: Optional[str] = None
    ) -> int:
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
        organisation_id: Optional[str] = None,
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
    def list_by_model(
        self, embedding_model: str, organisation_id: Optional[str] = None
    ) -> list[SemanticIndexRecord]:
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
    def list_all(
        self, organisation_id: Optional[str] = None
    ) -> list[SemanticIndexRecord]:
        """Retrieve all records.

        Returns
        -------
        list[SemanticIndexRecord]
            All records, sorted deterministically.
        """
        ...

    @abstractmethod
    def count(self, organisation_id: Optional[str] = None) -> int:
        """Count total records in the repository.

        Returns
        -------
        int
            Record count.
        """
        ...

    @abstractmethod
    def search_similar(
        self, query_embedding: list[float], embedding_model: str,
        representation_version: str, top_k: int, min_similarity: float,
        organisation_id: Optional[str] = None,
    ) -> list[tuple[SemanticIndexRecord, float]]:
        """Search active model/version vectors by application-side similarity."""
        ...

    def count_by_model(
        self, embedding_model: str, organisation_id: Optional[str] = None
    ) -> int:
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
        return len(self.list_by_model(embedding_model, organisation_id))


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
        # Key: (organisation_id, evidence_id, embedding_model, representation_version)
        self._records: dict[tuple[str, str, str, str], SemanticIndexRecord] = {}

    @staticmethod
    def _key(record: SemanticIndexRecord) -> tuple[str, str, str, str]:
        return (
            record.organisation_id,
            record.evidence_id,
            record.embedding_model,
            record.representation_version,
        )

    def _scope(self, record: SemanticIndexRecord, organisation_id: Optional[str]) -> bool:
        return organisation_id is None or record.organisation_id == organisation_id

    def get_by_composite_key(
        self,
        evidence_id: str,
        embedding_model: str,
        representation_version: str,
        organisation_id: Optional[str] = None,
    ) -> Optional[SemanticIndexRecord]:
        """Retrieve record by composite key."""
        if organisation_id is not None:
            return self._records.get(
                (organisation_id, evidence_id, embedding_model, representation_version)
            )
        for key, record in self._records.items():
            if key[1:] == (evidence_id, embedding_model, representation_version):
                return record
        return None

    def get_by_evidence_id(
        self, evidence_id: str, organisation_id: Optional[str] = None
    ) -> list[SemanticIndexRecord]:
        """Retrieve all records for a given evidence_id."""
        results = [
            record
            for record in self._records.values()
            if record.evidence_id == evidence_id and self._scope(record, organisation_id)
        ]
        # Sort deterministically by (model, version, evidence_id)
        return sorted(
            results,
            key=lambda r: (r.embedding_model, r.representation_version, r.evidence_id),
        )

    def upsert(self, record: SemanticIndexRecord) -> SemanticIndexRecord:
        """Insert or update a record."""
        self._records[self._key(record)] = record
        return record

    def delete(
        self,
        evidence_id: str,
        embedding_model: str,
        representation_version: str,
        organisation_id: Optional[str] = None,
    ) -> bool:
        """Delete a record by composite key."""
        if organisation_id is not None:
            key = (organisation_id, evidence_id, embedding_model, representation_version)
            if key in self._records:
                del self._records[key]
                return True
            return False
        keys = [
            key for key in self._records
            if key[1:] == (evidence_id, embedding_model, representation_version)
        ]
        for key in keys:
            del self._records[key]
        return bool(keys)

    def delete_by_evidence_id(
        self, evidence_id: str, organisation_id: Optional[str] = None
    ) -> int:
        """Delete all records for a given evidence_id."""
        keys_to_delete = [
            key for key, record in self._records.items()
            if key[1] == evidence_id and self._scope(record, organisation_id)
        ]
        for key in keys_to_delete:
            del self._records[key]
        return len(keys_to_delete)

    def exists(
        self,
        evidence_id: str,
        embedding_model: str,
        representation_version: str,
        organisation_id: Optional[str] = None,
    ) -> bool:
        """Check if a record exists by composite key."""
        if organisation_id is not None:
            return (organisation_id, evidence_id, embedding_model, representation_version) in self._records
        return any(
            key[1:] == (evidence_id, embedding_model, representation_version)
            for key in self._records
        )

    def list_by_model(
        self, embedding_model: str, organisation_id: Optional[str] = None
    ) -> list[SemanticIndexRecord]:
        """Retrieve all records for a given embedding model."""
        results = [
            record
            for record in self._records.values()
            if record.embedding_model == embedding_model
            and self._scope(record, organisation_id)
        ]
        # Sort deterministically
        return sorted(results, key=lambda r: (r.evidence_id, r.representation_version))

    def list_all(
        self, organisation_id: Optional[str] = None
    ) -> list[SemanticIndexRecord]:
        """Retrieve all records."""
        results = [
            record for record in self._records.values()
            if self._scope(record, organisation_id)
        ]
        # Sort deterministically
        return sorted(
            results, key=lambda r: (r.embedding_model, r.evidence_id, r.representation_version)
        )

    def count(self, organisation_id: Optional[str] = None) -> int:
        """Count total records."""
        if organisation_id is None:
            return len(self._records)
        return sum(1 for r in self._records.values() if r.organisation_id == organisation_id)

    def search_similar(self, query_embedding, embedding_model, representation_version, top_k, min_similarity, organisation_id=None):
        matches = []
        for record in self._records.values():
            if record.embedding_model != embedding_model or record.representation_version != representation_version:
                continue
            if not self._scope(record, organisation_id):
                continue
            score = _similarity(query_embedding, record.embedding)
            if score >= min_similarity:
                matches.append((record, score))
        return sorted(matches, key=lambda item: (-item[1], item[0].evidence_id))[:top_k]


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
        self._file_lock = _FileLock(self._path.with_suffix(self._path.suffix + ".lock"))
        self._records: dict[tuple[str, str, str, str], SemanticIndexRecord] = {}
        # Construct with the same degradation policy as every read: a missing
        # or corrupt durable document becomes an empty snapshot instead of
        # crashing an otherwise-read-only caller (P11).
        self._refresh()

    @staticmethod
    def _key(record: SemanticIndexRecord) -> tuple[str, str, str, str]:
        return (
            record.organisation_id,
            record.evidence_id,
            record.embedding_model,
            record.representation_version,
        )

    @staticmethod
    def _in_scope(record: SemanticIndexRecord, organisation_id: Optional[str]) -> bool:
        return organisation_id is None or record.organisation_id == organisation_id

    def _load(self) -> None:
        if not self._path.exists():
            self._records = {}
            return
        with self._path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, list):
            raise ValueError("semantic index file must contain a JSON list")
        fresh: dict[tuple[str, str, str, str], SemanticIndexRecord] = {}
        for item in payload:
            record = SemanticIndexRecord.model_validate(item)
            fresh[self._key(record)] = record
        self._records = fresh

    def _refresh(self) -> None:
        """Re-read the durable JSON document into the in-memory snapshot.

        Called under the OS file lock so every operation observes the latest
        committed state committed by any process — not a stale per-process
        snapshot.  Missing/corrupt files degrade to an empty snapshot rather
        than crashing a read-only caller; malformed JSON (a partial write from
        a non-cooperating process, or hand-editing) is treated as an empty
        index to favour availability over a permanent error.
        """
        try:
            self._load()
        except (json.JSONDecodeError, OSError, ValueError):
            self._records = {}

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = [
            record.model_dump(mode="json")
            for record in sorted(
                self._records.values(),
                key=lambda item: (
                    item.embedding_model,
                    item.organisation_id,
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
        organisation_id: Optional[str] = None,
    ) -> Optional[SemanticIndexRecord]:
        with self._file_lock:
            with self._lock:
                self._refresh()
                if organisation_id is not None:
                    return self._records.get(
                        (organisation_id, evidence_id, embedding_model, representation_version)
                    )
                for key, record in self._records.items():
                    if key[1:] == (evidence_id, embedding_model, representation_version):
                        return record
                return None

    def get_by_evidence_id(
        self, evidence_id: str, organisation_id: Optional[str] = None
    ) -> list[SemanticIndexRecord]:
        with self._file_lock:
            with self._lock:
                self._refresh()
                records = [
                    record for record in self._records.values()
                    if record.evidence_id == evidence_id
                    and self._in_scope(record, organisation_id)
                ]
                return sorted(records, key=lambda item: (item.embedding_model, item.representation_version))

    def upsert(self, record: SemanticIndexRecord) -> SemanticIndexRecord:
        with self._file_lock:
            with self._lock:
                self._refresh()
                self._records[self._key(record)] = record
                self._persist()
                return record

    def delete(
        self,
        evidence_id: str,
        embedding_model: str,
        representation_version: str,
        organisation_id: Optional[str] = None,
    ) -> bool:
        with self._file_lock:
            with self._lock:
                self._refresh()
                if organisation_id is not None:
                    key = (organisation_id, evidence_id, embedding_model, representation_version)
                    if key not in self._records:
                        return False
                    del self._records[key]
                    self._persist()
                    return True
                keys = [
                    key for key in self._records
                    if key[1:] == (evidence_id, embedding_model, representation_version)
                ]
                for key in keys:
                    del self._records[key]
                if keys:
                    self._persist()
                return bool(keys)

    def delete_by_evidence_id(
        self, evidence_id: str, organisation_id: Optional[str] = None
    ) -> int:
        with self._file_lock:
            with self._lock:
                self._refresh()
                keys = [
                    key for key, record in self._records.items()
                    if key[1] == evidence_id and self._in_scope(record, organisation_id)
                ]
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
        organisation_id: Optional[str] = None,
    ) -> bool:
        with self._file_lock:
            with self._lock:
                self._refresh()
                if organisation_id is not None:
                    return (organisation_id, evidence_id, embedding_model, representation_version) in self._records
                return any(
                    key[1:] == (evidence_id, embedding_model, representation_version)
                    for key in self._records
                )

    def list_by_model(
        self, embedding_model: str, organisation_id: Optional[str] = None
    ) -> list[SemanticIndexRecord]:
        with self._file_lock:
            with self._lock:
                self._refresh()
                records = [
                    record for record in self._records.values()
                    if record.embedding_model == embedding_model
                    and self._in_scope(record, organisation_id)
                ]
                return sorted(records, key=lambda item: (item.evidence_id, item.representation_version))

    def list_all(
        self, organisation_id: Optional[str] = None
    ) -> list[SemanticIndexRecord]:
        with self._file_lock:
            with self._lock:
                self._refresh()
                records = [
                    record for record in self._records.values()
                    if self._in_scope(record, organisation_id)
                ]
                return sorted(
                    records,
                    key=lambda item: (item.embedding_model, item.evidence_id, item.representation_version),
                )

    def count(self, organisation_id: Optional[str] = None) -> int:
        with self._file_lock:
            with self._lock:
                self._refresh()
                if organisation_id is None:
                    return len(self._records)
                return sum(1 for r in self._records.values() if r.organisation_id == organisation_id)

    def search_similar(self, query_embedding, embedding_model, representation_version, top_k, min_similarity, organisation_id=None):
        with self._file_lock:
            with self._lock:
                self._refresh()
                matches = []
                for record in self._records.values():
                    if record.embedding_model != embedding_model or record.representation_version != representation_version:
                        continue
                    if not self._in_scope(record, organisation_id):
                        continue
                    score = _similarity(query_embedding, record.embedding)
                    if score >= min_similarity:
                        matches.append((record, score))
                return sorted(matches, key=lambda item: (-item[1], item[0].evidence_id))[:top_k]
