"""Factory for the one coherent primary source-of-truth backend."""

from app.core.config import settings
from app.persistence.sqlite_store import SQLiteSourceStore


def build_sqlite_source_store() -> SQLiteSourceStore:
    if settings.source_repository_backend.lower() != "database":
        raise RuntimeError("SQLite source store requested while database backend is disabled")
    return SQLiteSourceStore(settings.source_database_path)