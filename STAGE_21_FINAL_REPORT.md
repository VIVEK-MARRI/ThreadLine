# Stage 21 Final Report: Durable Semantic Storage & Automatic Index Integration

## 1. Baseline and final validation

- Exact baseline before Stage 21: **724 passed, 56 warnings**
- Final suite: **735 passed, 0 warnings**
- New tests: **11 meaningful tests**
- Application import: `app import OK`
- Touched Python files: no editor diagnostics

The 56 warnings were project-owned `datetime.utcnow()` uses in the Stage 19 semantic and hybrid tests. They were replaced with timezone-aware UTC timestamps without changing historical fixture meaning.

## 2. Durable backend decision

ThreadLine has no existing database, ORM, migration framework, or durable source repository. Adding PostgreSQL only for derived embeddings would create an inconsistent dual-persistence architecture. Stage 21 therefore adds `JsonFileSemanticIndexRepository` while preserving `InMemorySemanticIndexRepository` as the offline test/development fallback.

The JSON adapter is durable for the semantic-derived data, not a replacement for the in-memory organisational source repositories. Its file path is configurable with `SEMANTIC_INDEX_PATH`; the backend is selected with `SEMANTIC_INDEX_BACKEND=in_memory|persistent`.

## 3. Repository and storage details

`JsonFileSemanticIndexRepository` implements the existing `AbstractSemanticIndexRepository` contract:

- upsert
- get by composite key
- get by evidence ID
- delete
- delete by evidence ID
- existence checks
- list by model
- list all
- count
- deterministic ordering

Records persist:

- `evidence_id`
- `embedding`
- `embedding_model`
- `embedding_dimension`
- `representation_hash`
- `representation_version`
- `source_reference`
- `indexed_at`

The composite identity `(evidence_id, embedding_model, representation_version)` is enforced by the repository's keyed record map and serialized as one logical record per key. Upsert replaces the existing record rather than creating duplicates. Writes use a process-local lock, write a complete temporary JSON document, flush it, call `fsync`, and atomically replace the target file with `os.replace`. Queries cannot observe a partially written vector.

There is no database migration because the project has no database architecture. Empty-file initialization is safe and non-destructive.

## 4. Automatic indexing trigger

The existing `EvidenceRetrievalService` is the owning evidence boundary: it constructs authoritative `EvidenceItem` objects from structured services. It now accepts an optional `SemanticIndexingService` and indexes the items after structured construction. Indexing errors are isolated by the Stage 20 batch behavior.

This is the safest available lifecycle integration because ThreadLine does not currently persist a reusable `EvidenceItem` collection. It does not index raw transcripts and it does not create organisational facts.

## 5. Query-time integration

The API query dependency now constructs:

1. configured semantic repository
2. configured embedding provider
3. `SemanticIndexingService`
4. `SemanticEvidenceRetrievalService` with repository, active model, and active version
5. `HybridEvidenceRetrievalService`
6. `NaturalLanguageQueryService` with the hybrid collaborator

The hybrid path first obtains structured evidence, automatically ensures current items are indexed, then compares the query vector against persisted vectors. The returned `SemanticEvidenceMatch` carries the original source `EvidenceItem`; the semantic index is never used to reconstruct text, provenance, timestamps, or metadata.

When no repository is configured, Stage 19's original in-memory semantic behavior remains available for compatibility.

## 6. Missing and stale records

For repository-backed semantic retrieval:

- missing index records are skipped
- representation-hash mismatches are skipped
- no embedding call is made for an evidence item during read-only search
- structured evidence remains available
- no fabricated vector is created

This chooses correctness over query-time re-embedding. Automatic indexing occurs at the structured evidence boundary; a later retry can refresh failed or stale records.

## 7. Retry and repair behavior

`SemanticIndexingService.retry_indexing(evidence_item)` retries from authoritative source evidence. It is idempotent and reuses a valid unchanged vector. Embedding generation happens before repository upsert, so a provider failure leaves any previous valid record untouched.

`rebuild_index(evidence_items)` remains deterministic and idempotent, removes stale IDs for the active model/version, and indexes the supplied source snapshot. `check_consistency(source_evidence)` is read-only and now detects:

- missing source evidence / orphan records
- stale representation hashes
- unexpected active model
- unexpected active representation version
- empty vectors
- dimension mismatches
- invalid values
- NaN
- infinity

The repository key prevents duplicate logical records in the durable adapter.

## 8. Model and representation migrations

Configuration explicitly selects:

- `EMBEDDING_PROVIDER`
- `ACTIVE_EMBEDDING_MODEL`
- `ACTIVE_REPRESENTATION_VERSION`

A model or representation migration writes new records under a new composite key. Existing records remain available for cleanup, but queries select exactly one active model/version pair and never compare incompatible vector spaces. The old records are not source evidence and may be removed after migration verification.

## 9. Startup and API safety

Application startup only constructs the configured repository and services. It does not scan the corpus or perform mass re-embedding. No unrestricted reindex endpoint was added. Repair and retry remain service-level/internal operations.

## 10. Concurrency and failure semantics

The JSON adapter serializes writes within a process and atomically replaces the complete file. Concurrent processes sharing the same file are not a supported deployment mode yet; a database-backed adapter with transactional uniqueness would be required for that scenario.

Two same-key writes in one process are serialized and the last successful upsert wins. A query sees either the previous complete vector or the next complete vector, never a partial vector. If provider generation fails, source retrieval continues and the previous index record is preserved.

## 11. Read-only source invariant

Verified by existing and new tests: semantic indexing and deletion only mutate derived semantic records. They do not mutate or delete entities, mentions, meetings, temporal state, memory, insights, attention, actions, relationships, dependencies, organisation changes, portfolio data, or source `EvidenceItem` objects.

Deleting an index record does not delete source evidence.

## 12. Performance smoke measurement

Synthetic run using Python 3.13, `FakeEmbeddingProvider(dimension=32)`, 1,000 generated `EvidenceItem` objects, and the JSON repository:

- initial indexing: **45.981 seconds**
- unchanged re-indexing: **0.007 seconds**
- final records: **1,000**

The unchanged run demonstrates effective hash-based embedding reuse. Initial JSON performance is limited by the current repository contract persisting the complete file after each individual upsert. This is not a production-scale claim; a future durable backend or batch transaction should add bulk commit support before larger deployments.

## 13. Files created

- `app/repositories/semantic_index_repository.py` updated with `JsonFileSemanticIndexRepository`
- `tests/test_semantic_index_database.py`
- `tests/test_semantic_index_integration.py`
- `STAGE_21_FINAL_REPORT.md`

## 14. Files modified

- `app/core/config.py`
- `app/services/semantic_evidence_retrieval_service.py`
- `app/services/semantic_indexing_service.py`
- `app/services/evidence_retrieval_service.py`
- `app/services/hybrid_evidence_retrieval_service.py`
- `app/services/natural_language_query_service.py`
- `app/api/query.py`
- `tests/test_hybrid_retrieval.py`
- `tests/test_semantic_retrieval.py`
- `README.md`

## 15. Test results

Full suite:

```text
735 passed in 4.19s
```

Required semantic regression suites:

```text
99 passed in 0.56s
```

Required organisational regression suites:

```text
138 passed in 1.62s
```

Natural-language suites:

```text
25 passed in 0.70s
```

Stage 21 durable/integration plus Stage 20 indexing tests:

```text
40 passed in 0.36s
```

Stage 21 is complete within the current in-memory source architecture. The durable JSON adapter is intentionally a derived-index persistence layer, not a claim that ThreadLine now has a fully durable organisational database.
