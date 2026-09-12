# Stage 23.1 Final Report: Core Pipeline Correctness, Worker Wiring & Semantic Retrieval Repair

## 1. Validation baseline and final result

Exact baseline before 23.1:

```text
776 passed in 4.54s
```

Final result:

```text
784 passed in 4.53s
```

Warnings: **0**

New focused tests added: **8**

- 5 corpus-wide semantic retrieval tests
- 3 pipeline correctness tests

Additional focused repair slice: 108 tests passed before the final additions.

Application import:

```text
app import OK
```

## 2. Semantic corpus search architecture

Before 23.1, repository-backed semantic search iterated only over the `evidence_items` passed by structured retrieval. That meant semantic retrieval could not discover evidence structured retrieval did not return.

The repaired flow is:

```text
query
  -> query embedding
  -> AbstractSemanticIndexRepository.search_similar(...)
  -> active model/version records
  -> authoritative EvidenceItem source lookup
  -> stale/orphan validation
  -> hybrid merge and deterministic ranking
```

`InMemorySemanticIndexRepository` and `JsonFileSemanticIndexRepository` now perform application-side vector similarity over all records for the selected model and representation version. No vector database was introduced.

The application hybrid service receives an authoritative corpus provider built by `EvidenceRetrievalService.build_semantic_corpus()`. The corpus is assembled from existing evidence-building services and deduplicated by `evidence_id`; it is not reconstructed from vector metadata.

## 3. Persisted semantic lookup behavior

`SemanticEvidenceRetrievalService.search_persisted()`:

- embeds only the query
- reads persisted vectors
- selects exactly one configured model/version
- searches the full available corpus
- resolves each ID through authoritative source evidence
- checks the current representation hash
- excludes stale records
- excludes orphan records
- never writes the semantic repository
- never generates an evidence embedding during query

The repository query requests all active candidates before source/staleness filtering so stale or orphan records cannot crowd valid records out of the top-k result set.

## 4. Source EvidenceItem resolution

Semantic results carry the original authoritative `EvidenceItem` returned by the source lookup. Vector metadata and `source_reference` are never used to reconstruct source text, timestamps, entities, or provenance.

If an ID cannot be rehydrated, that result is discarded safely.

## 5. Hybrid and entity safety

Hybrid retrieval remains additive and structured evidence remains authoritative. Unknown intents return immediately without semantic search. Ambiguous entity resolution skips semantic retrieval.

For resolved entities, persisted semantic candidates are conservatively limited to same-entity evidence or evidence already present in structured results. Semantic similarity does not create relationships or silently resolve entities.

## 6. Query read-only guarantee

The `EvidenceRetrievalService` no longer accepts a semantic-index writer dependency. Query construction no longer supplies `SemanticIndexingService`. The query path can read persisted semantic vectors but does not index, write source records, or create jobs.

The previous hidden indexing side effect was removed. Semantic indexing belongs to the processing worker path.

## 7. Worker application wiring

FastAPI now uses a lifespan context. When `BACKGROUND_WORKER_ENABLED=true`, startup creates and starts a daemon `BackgroundWorkerService` thread; shutdown calls cooperative worker shutdown, signals the loop, and joins it with a bounded timeout.

Default configuration remains worker-disabled:

```text
BACKGROUND_WORKER_ENABLED=false
BACKGROUND_POLL_INTERVAL_SECONDS=1.0
BACKGROUND_MAX_ATTEMPTS=3
BACKGROUND_LEASE_SECONDS=60
```

Smoke validation succeeded for both disabled/default mode and explicit worker-enabled start/stop mode.

## 8. Processing handlers and checkpoints

Meeting processing uses `MeetingProcessingService` with explicit handlers for:

- `EXTRACTED`: invokes the existing extraction service
- `RESOLVED`: explicit processing boundary
- `RELATIONSHIPS_PERSISTED`: explicit processing boundary
- `DERIVED_INTELLIGENCE`: explicit processing boundary
- `SEMANTIC_INDEXED`: explicit processing boundary

The first handler is connected to the existing extraction service; later boundaries are explicit extension points because the current extraction model does not yet contain the entity/mention facts required to safely replay resolution and relationship persistence.

A missing handler now raises `PermanentJobError` and does not checkpoint. `COMPLETED` is only written after all required handlers succeed. Checkpoint failures propagate rather than producing false stage success.

## 9. Claim and transition atomicity

Worker polling now continues through candidate jobs if another worker wins a claim race instead of abandoning the polling cycle after the first `None` claim.

SQLite transitions use conditional updates with the expected current status in the `WHERE` clause and verify affected row count. This prevents a worker from validating one state and later overwriting a competing state transition.

The SQLite store serializes write transactions with its process-local reentrant lock. The existing shared connection remains process-local and is not claimed as a distributed database connection pool.

## 10. Lease and recovery behavior

Claims persist worker ID, start time, attempt count, and lease expiry. Expired running jobs become retryable when attempts remain and become terminal `FAILED` jobs at the attempt limit. `SUCCEEDED`, `FAILED`, and `CANCELLED` jobs are never reclaimed.

Shutdown stops new claims and does not mark an active job successful. A crashed worker leaves lease state for later recovery.

## 11. Meeting conflict behavior

Stable meeting ingestion now has explicit semantics:

- same `meeting_id` plus identical payload: returns the existing meeting idempotently
- same `meeting_id` plus different title, transcript, date, or participants: raises `MeetingConflictError`
- API translation: HTTP `409 Conflict`
- legacy requests without a meeting ID continue to receive UUIDs

No conflicting source payload is silently overwritten.

## 12. Current-time and event-time semantics

The query and processing boundaries accept a reference time and pass it through existing services. Event timestamps remain source/historical timestamps; reference time is used only for stale/current evaluation.

Stale epoch-sentinel documentation was removed. Missing event times are described as unset rather than as a fabricated historical date.

Repository audit found no ThreadLine-owned `datetime.utcnow`, `_EPOCH_UTC`, `1970-01-01`, or epoch-sentinel references after cleanup.

## 13. Documentation

README now describes:

- Stages 1–23.1
- SQLite source of truth
- JSON semantic derived index
- full active-corpus semantic retrieval
- authoritative source rehydration
- durable jobs and process-local worker
- lifespan startup/shutdown
- read-only query behavior
- explicit non-distributed limitation

## 14. Focused semantic tests

Added [tests/test_semantic_corpus_search.py](tests/test_semantic_corpus_search.py):

- structured-independent semantic discovery
- unrelated evidence exclusion
- stale vector exclusion without writes
- active model/version isolation
- missing source discard

Added [tests/test_pipeline_correctness.py](tests/test_pipeline_correctness.py):

- identical meeting idempotency
- conflicting meeting payload rejection
- missing handler failure without checkpoint

## 15. Files created or modified

Created:

- `tests/test_semantic_corpus_search.py`
- `tests/test_pipeline_correctness.py`
- `STAGE_23_1_FINAL_REPORT.md`

Modified:

- `app/repositories/semantic_index_repository.py`
- `app/services/semantic_evidence_retrieval_service.py`
- `app/services/evidence_retrieval_service.py`
- `app/services/hybrid_evidence_retrieval_service.py`
- `app/api/query.py`
- `app/services/meeting_processing_service.py`
- `app/repositories/background_job_repository.py`
- `app/services/background_worker_service.py`
- `app/services/meeting_service.py`
- `app/api/meetings.py`
- `app/main.py`
- `app/services/evidence_context_builder.py`
- `app/services/organisation_change_intelligence_service.py`
- `README.md`

## 16. Exact final validation

Full suite:

```text
784 passed in 4.53s
```

Structured intelligence regression group:

```text
138 passed in 0.90s
```

Semantic and natural-language repair slice:

```text
71 passed in 0.73s
```

Worker and meeting repair slice:

```text
37 passed in 1.74s
```

Database+persistent-semantic startup:

```text
SQLiteMeetingRepository {'source_backend': 'database', ...} database import OK
```

Worker-enabled lifecycle:

```text
worker enabled lifecycle OK
```

Stage 23.1 is complete within the current architecture. The implementation does not claim distributed execution or exactly-once processing.
