# Stage 23.2 Final Report: Real Meeting Worker Orchestration

## 1. Baseline

The reported Stage 23.1 baseline was **784 passed, 0 warnings**. In this
workspace the first attempted run was blocked by two local-environment issues:

- the shell `python` executable is Python 3.4 without pytest;
- the project virtual environment inherited `DEBUG=release`, which is not a
  valid boolean setting.

With `.venv\\Scripts\\python.exe`, `DEBUG=false`, cache provider disabled, and
an isolated writable pytest base directory, the pre-change suite was runnable.

## 2. Processing dependency map discovered

```text
MeetingService -> MeetingRepository
  -> BackgroundJobScheduler -> SQLiteBackgroundJobRepository
  -> BackgroundWorkerService claim
  -> ExtractionService -> ExtractionRepository
  -> ResolutionService -> CandidateScoringService -> lexical generator/scorer
     -> MentionRepository
  -> DependencyResolutionService -> explicit keyword extraction
     -> DependencyRepository
  -> correlation / temporal / memory / insight / attention / actions /
     timeline / relationships / dependency graph / impact / change / portfolio
     read models
  -> EvidenceRetrievalService.build_semantic_corpus
  -> SemanticIndexingService -> SemanticIndexRepository
  -> COMPLETED / SUCCEEDED
```

## 3. Implemented

- Added `MeetingPipelineOrchestrator`, a thin coordinator with no duplicated
  extraction, resolution, relationship, intelligence, or embedding rules.
- Wired every worker checkpoint in `app.main` to a real existing service.
- `EXTRACTED` uses `ExtractionService`; its repository upsert completes before
  the checkpoint.
- `RESOLVED` runs `ResolutionService` over durable mentions for the meeting.
- `RELATIONSHIPS_PERSISTED` runs `DependencyResolutionService`, which accepts
  only explicit dependency statements and uses deterministic dependency IDs.
- `DERIVED_INTELLIGENCE` evaluates the established derived read-model graph.
- `SEMANTIC_INDEXED` builds authoritative evidence using
  `EvidenceRetrievalService` and calls `SemanticIndexingService` per item, so
  embedding failures propagate rather than being swallowed by batch indexing.
- Added Stage 23.2 README documentation and eight real-service orchestration
  tests, including retry checkpoint boundaries, reprocessing idempotency, and
  SQLite/persistent-semantic-index reopen verification.

## 4. Verification

Focused worker/persistence/semantic slice before the final durable restart test:

```text
78 passed in 1.16s
```

Stage 23.2 orchestration tests:

```text
8 passed in 0.44s
```

Final complete suite:

```text
792 passed in 10.62s
```

Warnings: **0** (pytest cache provider disabled because the shared workspace
cache is permission-locked).

## 5. End-to-end and recovery result

The full worker test proves extraction persistence, resolution, explicit
dependency persistence, derived-service invocation, semantic-index upsert,
`COMPLETED`, and `SUCCEEDED`. The durable test closes/reopens SQLite-backed
source repositories and the persistent JSON semantic index and verifies the
extraction, dependency, and vector record remain available.

Reprocessing uses existing dependency and semantic upserts, so it produces no
duplicate dependency or semantic-index record. Failed stages preserve the last
successful checkpoint and leave the job retryable.

The existing query path remains read-only; no query code was changed and the
worker alone invokes semantic-index writes.

## 6. Not implemented / future work

- ExtractionResult has no entity-mention representation. The worker therefore
  resolves only mentions already durably recorded for the meeting; it does not
  fabricate mentions or canonical entities from issue/task/decision/risk text.
- Memory, insights, attention, actions, timelines, changes, portfolio, impact,
  and relationship graphs have no persistent derived repositories in the
  current architecture. They remain deterministic read models and are evaluated
  by the worker without inventing a second persistence model.
- Execution remains process-local. Durable state and recovery are implemented;
  distributed workers and exactly-once distributed execution are not claimed.
