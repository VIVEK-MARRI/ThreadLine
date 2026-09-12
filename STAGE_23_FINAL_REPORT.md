# Stage 23 Final Report: Reliable Asynchronous Processing & Production Workflow

## 1. Test baseline and final result

Exact baseline before Stage 23:

```text
752 passed in 6.18s
```

Final result:

```text
776 passed in 7.77s
```

Warnings: **0**

New Stage 23 tests: **24 meaningful tests**. The five requested Stage 23 suites pass completely.

Application import and diagnostics:

```text
app import OK
{'source_backend': 'in_memory', 'source_database': True, 'semantic_index': True, 'background_jobs': True, ...}
```

## 2. Durable job repository

Added `AbstractBackgroundJobRepository` with:

- deterministic idempotent enqueue
- get/list
- atomic claim
- validated transitions
- checkpoints
- stale lease recovery
- cancellation
- queue counts and oldest-pending diagnostics

Implementations:

- `InMemoryBackgroundJobRepository` for offline tests
- `SQLiteBackgroundJobRepository` using the existing shared `SQLiteSourceStore`

The repository does not store executable function names or arbitrary code. Job types are finite `BackgroundJobType` values.

## 3. Database migration

SQLite schema version increased from 1 to 2. The migration adds `background_jobs` with:

- `job_id` primary key
- `job_type`
- `payload_id`
- `status`
- `attempts`
- `max_attempts`
- `created_at`
- `started_at`
- `completed_at`
- `last_error`
- `error_type`
- `next_retry_at`
- `lease_until`
- `worker_id`
- `stage`

An index supports status/retry-time/creation-time polling. The migration is additive, idempotent, and non-destructive. Existing Stage 22 databases open successfully and receive version 2.

## 4. State machine

Supported states:

- `PENDING`
- `RUNNING`
- `RETRY_WAITING`
- `SUCCEEDED`
- `FAILED`
- `CANCELLED`

Allowed transitions:

```text
PENDING       -> RUNNING | CANCELLED
RUNNING       -> SUCCEEDED | RETRY_WAITING | FAILED
RETRY_WAITING -> RUNNING | CANCELLED
```

Terminal states cannot be mutated into another state. `COMPLETED` remains a compatibility alias for `SUCCEEDED` for Stage 22 callers.

## 5. Job identity and enqueue

`BackgroundJobScheduler` uses:

```text
job_id = f"{job_type.value}:{payload_id}"
```

Therefore ten enqueue requests for the same meeting-processing pair produce one logical job. SQLite uses `ON CONFLICT(job_id) DO NOTHING`; the in-memory repository uses keyed insertion.

Meeting ingestion persists the source meeting first and then enqueues `MEETING_PROCESSING`. A source persistence failure cannot create a processing job.

## 6. Atomic claiming and leases

SQLite claiming uses a transaction and conditional status update. Only `PENDING` or due `RETRY_WAITING` jobs can be claimed. A second worker receives no claim after the first worker transitions the job to `RUNNING`.

Claims persist:

- worker ID
- attempt number
- start time
- lease expiry

`recover_stale(now)` moves expired `RUNNING` jobs to `RETRY_WAITING` when attempts remain, or to `FAILED` when the attempt limit is exhausted. This prevents crashed workers from leaving work permanently stuck.

The implementation provides SQLite process-level atomicity. It does not claim distributed exactly-once semantics across arbitrary machines or network filesystems.

## 7. Retry and failure classification

`BackgroundWorkerService` accepts an injected clock for deterministic tests and uses exponential backoff:

```text
backoff = backoff_seconds * 2 ** (attempt - 1)
```

`TransientJobError` and ordinary unexpected exceptions are bounded transient failures. `PermanentJobError` fails immediately without retry. All jobs stop at `max_attempts`.

Safe error metadata stores only:

- error type
- safe message
- attempt count
- stage

Stack traces are not written to the database.

## 8. Processing stages and checkpoints

`MeetingProcessingService` defines:

```text
INGESTED
EXTRACTED
RESOLVED
RELATIONSHIPS_PERSISTED
DERIVED_INTELLIGENCE
SEMANTIC_INDEXED
COMPLETED
```

The source meeting is already persisted before the job exists. Each completed handler stage writes a checkpoint. A retry starts after the stored checkpoint and does not repeat completed stages. Existing source IDs and upsert semantics preserve idempotency.

The stage service accepts handlers rather than embedding domain-specific AI behavior. No new intelligence, inference, or LLM layer was added.

## 9. Worker and shutdown behavior

`BackgroundWorkerService` provides:

- polling via `run_once`
- stale-job recovery
- atomic claiming through the repository
- handler execution
- retry scheduling
- state transitions
- checkpoints
- cooperative shutdown

`shutdown()` prevents new claims and does not blindly cancel a currently running handler. An unfinished claimed job remains lease-bound and becomes recoverable after lease expiry.

The worker is intentionally in-process. No Celery, Kafka, RabbitMQ, or distributed queue was introduced.

## 10. Failure isolation and semantic integration

Source-first guarantees:

- meeting persistence precedes job enqueue
- extraction failure leaves the meeting durable
- resolution failure may leave an unresolved mention
- dependency persistence uses existing deterministic IDs/upserts
- derived failure does not delete source data
- embedding failure does not delete source or previous valid semantic vectors

The semantic index remains derived JSON data. It is a worker-stage concern and is no longer triggered as a hidden side effect by the natural-language query dependency. Query retrieval only reads persisted vectors and authoritative source `EvidenceItem` objects.

## 11. Cancellation and diagnostics

Cancellation is allowed for `PENDING` and `RETRY_WAITING`. `RUNNING` jobs cannot be cancelled blindly.

Diagnostics:

```text
GET /api/v1/health/jobs
GET /health/diagnostics
```

They expose only:

- worker enabled flag
- counts by status
- oldest pending age
- stale-running count
- backend availability

They do not expose transcripts, credentials, provider prompts, or stack traces.

## 12. Security

Job payloads contain only stable IDs. No API keys, passwords, provider credentials, full transcripts, or executable function references are stored. Job types are validated enums. Error messages are safe operational strings.

## 13. Performance smoke measurements

Environment: Python 3.13.5, local Windows environment, in-memory job repository, one worker process. These are smoke measurements, not distributed production benchmarks.

- 100 deterministic enqueues: **0.001s**, 100 jobs
- 1,000 deterministic enqueues: **0.007s**, 1,000 jobs
- First claim and no-op execution from the 1,000-job set: **0.000408s**

No distributed throughput or exactly-once performance claim is made.

## 14. Files created

- `app/repositories/background_job_repository.py`
- `app/services/background_worker_service.py`
- `app/services/meeting_processing_service.py`
- `app/api/jobs.py`
- `tests/test_background_job_repository.py`
- `tests/test_background_worker.py`
- `tests/test_job_recovery.py`
- `tests/test_job_idempotency.py`
- `tests/test_processing_pipeline.py`
- `STAGE_23_FINAL_REPORT.md`

## 15. Files modified

- `app/models/background_job.py`
- `app/persistence/sqlite_store.py`
- `app/services/background_job_service.py`
- `app/api/meetings.py`
- `app/api/query.py`
- `app/main.py`
- `app/core/config.py`
- `app/main.py`
- `README.md`

## 16. Exact validation results

Full suite:

```text
776 passed in 7.77s
```

Stage 23 suites:

```text
24 passed in 1.55s
```

Natural-language and semantic compatibility run:

```text
71 passed in 1.63s
```

The final implementation is reliable within the current SQLite plus process-local worker architecture. It does not claim distributed execution, exactly-once processing, or zero data loss under machine failure.
