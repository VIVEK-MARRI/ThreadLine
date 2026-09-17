"""Single-file SQLite source-of-truth store for ThreadLine.

SQLite is used because the project has no existing ORM or database stack.
All primary repositories share this one store and schema; derived intelligence
and the Stage 21 semantic JSON index remain separate derived data.
"""

from contextlib import contextmanager
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Iterator


SCHEMA_VERSION = 6


class SQLiteSourceStore:
    """Own one SQLite connection and apply an idempotent schema migration."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            self.path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        # Serialize concurrent writers across connections/processes instead of
        # failing immediately with "database is locked".
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._migrate()

    def _migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_version "
                "(version INTEGER PRIMARY KEY)"
            )
            row = connection.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            ).fetchone()
            current = row[0] if row else 0
            if current < 1:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS meetings (
                        meeting_id TEXT PRIMARY KEY,
                        meeting_date TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS extraction_results (
                        meeting_id TEXT PRIMARY KEY REFERENCES meetings(meeting_id) ON DELETE CASCADE,
                        extracted_at TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS entities (
                        entity_id TEXT PRIMARY KEY,
                        entity_type TEXT NOT NULL,
                        canonical_name TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_entities_name
                        ON entities(entity_type, canonical_name);
                    CREATE TABLE IF NOT EXISTS entity_mentions (
                        mention_id TEXT PRIMARY KEY,
                        meeting_id TEXT NOT NULL REFERENCES meetings(meeting_id) ON DELETE CASCADE,
                        entity_id TEXT REFERENCES entities(entity_id) ON DELETE RESTRICT,
                        entity_type TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_mentions_meeting
                        ON entity_mentions(meeting_id);
                    CREATE INDEX IF NOT EXISTS idx_mentions_entity
                        ON entity_mentions(entity_id);
                    CREATE TABLE IF NOT EXISTS dependencies (
                        dependency_id TEXT PRIMARY KEY,
                        source_entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE RESTRICT,
                        target_entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE RESTRICT,
                        meeting_id TEXT NOT NULL REFERENCES meetings(meeting_id) ON DELETE CASCADE,
                        relationship_type TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_dependencies_source
                        ON dependencies(source_entity_id);
                    CREATE INDEX IF NOT EXISTS idx_dependencies_target
                        ON dependencies(target_entity_id);
                    INSERT INTO schema_version(version) VALUES (1);
                    """
                )
                current = 1
            if current < 2:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS background_jobs (
                        job_id TEXT PRIMARY KEY,
                        job_type TEXT NOT NULL,
                        payload_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        max_attempts INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT,
                        last_error TEXT,
                        error_type TEXT,
                        next_retry_at TEXT,
                        lease_until TEXT,
                        worker_id TEXT,
                        stage TEXT
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_jobs_claim "
                    "ON background_jobs(status, next_retry_at, created_at)"
                )
                connection.execute("INSERT INTO schema_version(version) VALUES (2)")
                current = 2
            if current < 3:
                connection.execute(
                    "ALTER TABLE meetings ADD COLUMN source_revision INTEGER NOT NULL DEFAULT 1"
                )
                connection.execute(
                    "ALTER TABLE background_jobs ADD COLUMN processing_revision TEXT"
                )
                connection.execute("INSERT INTO schema_version(version) VALUES (3)")
                current = 3
            if current < 4:
                for _table, _column in (
                    ("entity_mentions", "source_revision"),
                    ("dependencies", "source_revision"),
                    ("extraction_results", "source_revision"),
                ):
                    _cols = {
                        row[1]
                        for row in connection.execute(
                            f"PRAGMA table_info({_table})"
                        ).fetchall()
                    }
                    if _column not in _cols:
                        connection.execute(
                            f"ALTER TABLE {_table} ADD COLUMN {_column} INTEGER NOT NULL DEFAULT 1"
                        )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_mentions_meeting_revision "
                    "ON entity_mentions(meeting_id, source_revision)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_dependencies_meeting_revision "
                    "ON dependencies(meeting_id, source_revision)"
                )
                connection.execute("INSERT INTO schema_version(version) VALUES (4)")
                current = 4
            if current < 5:
                # Stage 24 tenant isolation.  Every tenant-owned table gains
                # organisation_id.  ADD COLUMN ... NOT NULL DEFAULT 'default'
                # is non-destructive: pre-tenant rows land in the explicit
                # bootstrap organisation ("default"), never in a real user's
                # organisation.  No FK to organisations(): legacy rows must
                # survive without a organisations row; scoping is enforced at
                # the repository boundary.
                for _table in (
                    "meetings",
                    "extraction_results",
                    "entities",
                    "entity_mentions",
                    "dependencies",
                    "background_jobs",
                ):
                    _cols = {
                        row[1]
                        for row in connection.execute(
                            f"PRAGMA table_info({_table})"
                        ).fetchall()
                    }
                    if "organisation_id" not in _cols:
                        connection.execute(
                            f"ALTER TABLE {_table} ADD COLUMN organisation_id "
                            "TEXT NOT NULL DEFAULT 'default'"
                        )
                # Backfill job tenant scope from the meeting each job processes
                # (payload_id == meeting_id for MEETING_PROCESSING).  Jobs
                # whose meeting is gone keep the bootstrap organisation.
                connection.execute(
                    "UPDATE background_jobs SET organisation_id = "
                    "(SELECT organisation_id FROM meetings "
                    "WHERE meetings.meeting_id = background_jobs.payload_id) "
                    "WHERE EXISTS (SELECT 1 FROM meetings "
                    "WHERE meetings.meeting_id = background_jobs.payload_id)"
                )
                for _index_ddl in (
                    "CREATE INDEX IF NOT EXISTS idx_meetings_org "
                    "ON meetings(organisation_id)",
                    "CREATE INDEX IF NOT EXISTS idx_entities_org_name "
                    "ON entities(organisation_id, entity_type, canonical_name)",
                    "CREATE INDEX IF NOT EXISTS idx_mentions_org_meeting "
                    "ON entity_mentions(organisation_id, meeting_id)",
                    "CREATE INDEX IF NOT EXISTS idx_mentions_org_entity "
                    "ON entity_mentions(organisation_id, entity_id)",
                    "CREATE INDEX IF NOT EXISTS idx_dependencies_org_source "
                    "ON dependencies(organisation_id, source_entity_id)",
                    "CREATE INDEX IF NOT EXISTS idx_dependencies_org_target "
                    "ON dependencies(organisation_id, target_entity_id)",
                    "CREATE INDEX IF NOT EXISTS idx_dependencies_org_meeting "
                    "ON dependencies(organisation_id, meeting_id)",
                    "CREATE INDEX IF NOT EXISTS idx_jobs_org_status "
                    "ON background_jobs(organisation_id, status, next_retry_at)",
                ):
                    connection.execute(_index_ddl)
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        user_id TEXT PRIMARY KEY,
                        email TEXT NOT NULL,
                        password_hash TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email
                        ON users(email);
                    CREATE TABLE IF NOT EXISTS organisations (
                        organisation_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        slug TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_organisations_slug
                        ON organisations(slug);
                    CREATE TABLE IF NOT EXISTS organisation_members (
                        member_id TEXT PRIMARY KEY,
                        organisation_id TEXT NOT NULL REFERENCES organisations(organisation_id) ON DELETE CASCADE,
                        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        role TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_members_org_user
                        ON organisation_members(organisation_id, user_id);
                    CREATE INDEX IF NOT EXISTS idx_members_user
                        ON organisation_members(user_id);
                    CREATE TABLE IF NOT EXISTS auth_sessions (
                        session_id TEXT PRIMARY KEY,
                        token_hash TEXT NOT NULL,
                        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        revoked_at TEXT
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_token
                        ON auth_sessions(token_hash);
                    CREATE INDEX IF NOT EXISTS idx_sessions_user
                        ON auth_sessions(user_id);
                    CREATE TABLE IF NOT EXISTS auth_login_attempts (
                        attempt_id TEXT PRIMARY KEY,
                        email TEXT NOT NULL,
                        attempted_at TEXT NOT NULL,
                        success INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE INDEX IF NOT EXISTS idx_attempts_email_time
                        ON auth_login_attempts(email, attempted_at);
                    CREATE TABLE IF NOT EXISTS security_events (
                        event_id TEXT PRIMARY KEY,
                        occurred_at TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        user_id TEXT,
                        organisation_id TEXT,
                        detail TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_security_events_time
                        ON security_events(occurred_at);
                    """
                )
                connection.execute("INSERT INTO schema_version(version) VALUES (5)")
            if current < 6:
                # Stage 34 proactive intelligence.  Durable per-scan result
                # payload on the job row itself (no new tables): the scan
                # handler writes a JSON summary (observed source watermark,
                # deterministic signal IDs, new-signal IDs) via
                # record_scan_result; later scans and the scan-status
                # endpoint read it back.  Nullable TEXT: pre-Stage-34 rows
                # and readers stay valid; absence of a summary simply means
                # "no scan result recorded".
                connection.execute(
                    "ALTER TABLE background_jobs ADD COLUMN result_summary TEXT"
                )
                connection.execute("INSERT INTO schema_version(version) VALUES (6)")
                current = 6

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN")
            try:
                yield self._connection
            except Exception:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def health(self) -> bool:
        try:
            with self._lock:
                self._connection.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def migration_version(self) -> int:
        row = self._connection.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return int(row[0]) if row else 0
