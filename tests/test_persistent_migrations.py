from app.persistence.sqlite_store import SCHEMA_VERSION, SQLiteSourceStore


def test_schema_migration_is_idempotent(tmp_path):
    path = tmp_path / "source.db"
    first = SQLiteSourceStore(path)
    assert first.migration_version() == SCHEMA_VERSION
    first.close()
    second = SQLiteSourceStore(path)
    assert second.migration_version() == SCHEMA_VERSION
    tables = {row[0] for row in second._connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"meetings", "entities", "entity_mentions", "dependencies", "schema_version"}.issubset(tables)


def test_foreign_keys_are_enabled(tmp_path):
    store = SQLiteSourceStore(tmp_path / "source.db")
    assert store._connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
