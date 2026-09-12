# Stage 22 Final Report: Durable Organisational Storage & Background Processing

## 1. Baseline and final result

Exact baseline before Stage 22:

```text
735 passed in 2.26s
```

Final default-mode result:

```text
752 passed in 7.00s
```

Warnings: **0**

New meaningful tests: **17**. The suite increased from 735 to 752 without padding tests.

Application checks:

```text
app import OK
1 True database app import OK
```

The second line is the clean database-mode startup check: schema version 1 and source health `True`.

## 2. Architecture decision

ThreadLine had no SQLAlchemy, SQLModel, database driver, migration framework, or coherent durable repository implementation. Its source repositories were independent in-memory stores. Adding PostgreSQL only for one subset would have created a fragmented architecture.

Stage 22 therefore introduces one coherent, opt-in SQLite source backend using Python's standard-library `sqlite3`. All primary source repositories share the same SQLite file and schema. The default remains `in_memory`, so unit tests and offline development do not require an external service.

Configuration:

```text
SOURCE_REPOSITORY_BACKEND=in_memory|database
SOURCE_DATABASE_PATH=.threadline/threadline.db
```

The Stage 21 JSON semantic index remains a separate derived store. It is not used as organisational source truth.

## 3. Primary versus derived data

Primary source data persisted by Stage 22:

- meetings
- extraction results
- canonical entities and aliases
- entity mentions and stored resolution state
- explicit dependencies

Derived or reconstructible data remains outside the source database:

- organisational memory
- timelines
- insights
- attention
- actions
- organisation changes
- impact analysis
- relationship graphs
- semantic embeddings

`CO_OCCURS_WITH` remains a computed association from durable mentions. It is not persisted as a causal or dependency fact. Explicit `DEPENDS_ON` and `BLOCKS` records remain authoritative source-backed relationship statements.

## 4. Database schema and migration

`app/persistence/sqlite_store.py` owns the single SQLite connection and schema initialization.

Schema version 1 creates:

- `schema_version`
- `meetings`
- `extraction_results`
- `entities`
- `entity_mentions`
- `dependencies`

Explicit indexes cover entity lookup, mentions by meeting/entity, and dependency source/target lookup. Foreign keys are enabled with `PRAGMA foreign_keys = ON`. Meeting deletion cascades to extraction and mentions; entity references in mentions/dependencies use restrictive foreign keys.

The migration is idempotent: `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, and a version row are used. It is non-destructive and does not mutate schema during ordinary application requests. A future schema change must add a higher versioned migration rather than silently altering existing tables.

This is a real versioned SQLite schema migration, but not an Alembic migration because the project has no ORM or migration framework to extend.

## 5. Repository implementations

The existing repository abstractions remain the service boundary. New adapters in `app/repositories/sqlite_source_repositories.py` implement:

- `AbstractMeetingRepository`
- `AbstractExtractionRepository`
- `AbstractEntityRepository`
- `AbstractMentionRepository`
- `AbstractDependencyRepository`

The API singleton wiring selects all five adapters from the same `SQLiteSourceStore` when database mode is enabled. In-memory implementations remain unchanged and remain the default.

Repositories serialize complete Pydantic payloads as JSON while also storing query/index columns. This preserves fields without changing domain models or business semantics.

## 6. Identity and idempotency

Existing IDs are preserved exactly:

- meeting IDs
- entity IDs
- mention IDs
- deterministic dependency IDs

`MeetingIngestRequest` now accepts an optional `meeting_id`. When supplied, `MeetingService` uses it as the stable identity and durable upsert key. Repeating the same request does not create a second meeting. Legacy requests without a supplied ID continue to receive UUIDs.

Entity and dependency repository writes are upserts by their existing IDs. Mention updates replace the same `mention_id`; historical resolution state is loaded as stored and is not recalculated during restart.

## 7. Transaction and failure boundaries

Each source repository write executes inside a SQLite transaction. Foreign-key violations roll back the attempted write and leave no partial dependency or mention record.

The ingestion lifecycle is intentionally source-first:

```text
persist meeting
  -> extraction/provider processing
  -> persist extraction/source records
  -> derived services
  -> semantic indexing
```

An extraction provider failure does not delete or roll back the already durable meeting. An embedding failure remains isolated to derived indexing. The source database is not made dependent on OpenAI or the embedding provider.

Multi-record domain workflows currently use staged repository operations because the existing service contracts are synchronous and separate. Foreign-key constraints and per-operation transactions prevent invalid partial records; a future full ingestion transaction can be added when extraction/entity persistence is unified into one service boundary.

## 8. Restart recovery

Verified with fresh store/repository instances:

- meeting survives repository recreation
- entity and aliases survive repository recreation
- mention survives with `AMBIGUOUS` status and no fabricated entity ID
- explicit dependency survives with relationship type and IDs intact
- relationship graph reconstructs from durable explicit dependency records

Deleting or rebuilding semantic JSON data does not touch these source tables.

## 9. Background processing

`app/models/background_job.py` defines:

- job ID
- job type
- status
- created/started/completed timestamps
- bounded attempts
- error text

Supported job types are `MEETING_PROCESSING`, `DERIVED_INTELLIGENCE_REBUILD`, and `SEMANTIC_INDEXING`.

`BackgroundJobService` is a lightweight in-process runner. Job identity is deterministic by `(job_type, payload_id)`, completed jobs are idempotent, retries are bounded, and lifecycle events are logged without transcript/provider-secret payloads.

This is intentionally not a distributed worker or queue. It is a safe processing abstraction for the current application architecture. Jobs are currently process-local and are not durable across process crashes; a future queue/database-backed job repository would be required for production multi-worker recovery.

## 10. Retry and failure isolation

Retries stop at the configured `max_attempts` (default 3). A failed job records its last error and becomes `FAILED`; it does not loop indefinitely. A transient failure can succeed on a subsequent bounded attempt.

Provider failure behavior:

- meeting persistence succeeds before extraction
- extraction failure preserves the meeting
- entity-resolution failure can preserve an unresolved mention
- embedding failure preserves source and prior valid semantic records
- derived rebuild failure does not delete source data

## 11. Health diagnostics

The existing `/health` endpoint remains compatible and returns the original response shape.

New internal endpoint:

```text
GET /health/diagnostics
```

It reports:

- configured source backend
- source database health
- semantic index availability
- background subsystem availability

It does not expose database credentials, connection strings, SQL errors, transcripts, or provider prompts.

## 12. Semantic index relationship

The architecture is now:

```text
SQLite primary source
  -> EvidenceItem and structured services
  -> SemanticIndexingService
  -> Stage 21 JSON semantic index
  -> Hybrid/natural-language retrieval
```

The semantic index remains derived. It stores vectors and index metadata only; authoritative source evidence supplies transcript text, provenance, timestamps, entity IDs, meeting IDs, and dependency context. It can be deleted and rebuilt from source evidence without source loss.

## 13. Data migration/import strategy

No automatic migration runs at startup. The source backend is opt-in and new deployments initialize an empty versioned SQLite schema.

For existing in-memory data, the safe import procedure is:

1. Export each repository's validated Pydantic records in deterministic ID order.
2. Create a new SQLite file and apply schema version 1.
3. Import meetings first, then entities, then mentions, extraction results, and explicit dependencies.
4. Let foreign keys reject missing references.
5. Treat same-ID identical payloads as idempotent; report same-ID conflicting payloads instead of silently overwriting them.
6. Rebuild derived intelligence and semantic embeddings only after source import validation.

This procedure preserves IDs and separates source migration from derived reconstruction. It is intentionally not executed automatically during application startup.

## 14. Performance smoke measurements

Environment: Python 3.13.5, local SQLite file, synchronous repository calls, standard-library `sqlite3`, no production-scale claim.

| Records | Writes | Primary-key reads |
|---:|---:|---:|
| 100 meetings | 0.288s | 0.001s |
| 1,000 meetings | 2.873s | 0.007s |

Each write uses a repository transaction and JSON payload serialization. These measurements are suitable as a smoke baseline, not a production benchmark. Bulk import and multi-process deployment would need additional transaction/session optimization.

## 15. Files created

- `app/persistence/__init__.py`
- `app/persistence/sqlite_store.py`
- `app/persistence/source_backend.py`
- `app/repositories/sqlite_source_repositories.py`
- `app/models/background_job.py`
- `app/services/background_job_service.py`
- `tests/test_persistent_meetings.py`
- `tests/test_persistent_entities.py`
- `tests/test_persistent_mentions.py`
- `tests/test_persistent_dependencies.py`
- `tests/test_persistent_relationships.py`
- `tests/test_persistent_migrations.py`
- `tests/test_ingestion_persistence.py`
- `tests/test_background_jobs.py`
- `STAGE_22_FINAL_REPORT.md`

## 16. Files modified

- `app/core/config.py`
- `app/models/meeting.py`
- `app/schemas/meeting.py`
- `app/services/meeting_service.py`
- `app/api/meetings.py`
- `app/api/entities.py`
- `app/main.py`
- `README.md`

## 17. Validation results

Full suite:

```text
752 passed in 7.00s
```

New Stage 22 suites:

```text
17 passed in 1.81s
```

Named natural-language and semantic regressions:

```text
124 passed in 0.80s
```

Named structured intelligence regressions:

```text
138 passed in 0.86s
```

Default application import:

```text
app import OK
```

Database-mode application import:

```text
1 True database app import OK
```

Stage 22 is complete within the current architecture. It provides a coherent durable source backend, preserves the existing repository/service boundaries, keeps semantic data derived, and adds bounded process-local background processing without pretending to provide a distributed queue.
