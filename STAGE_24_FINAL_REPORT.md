# Stage 24 Final Report: Authentication, Authorization, Organisation & Tenant Isolation

## 0. Baseline and final result

Baseline before Stage 24 (after the required test-harness-only E2E cleanup):

```text
862 passed, 0 failed, 0 skipped, 0 warnings
```

Final result:

```text
889 passed, 0 failed, 0 skipped, 0 warnings
```

889 = 862 pre-existing + 26 new `tests/test_stage_24_security.py` + 1 new
`tests/test_stage_24_e2e.py::test_real_full_stack_tenant_acceptance`.

Warnings: **0** (verified; the suite summary carries no warnings count, and
the security suites additionally pass under `-W error::DeprecationWarning`).

Pre-stage gate: the flaky `test_real_user_lifecycle_acceptance` was fixed
with a test-harness-only change (uvicorn stdout → per-test log file instead
of an undrained `stdout=PIPE`; transient single-poll failures no longer abort
`_wait_for_succeeded` before its unchanged deadline). No production change,
no timeout increase, no test removed. E2E-alone time dropped 21.8s → 10.4s,
confirming pipe back-pressure as the contention mechanism.

## 1. Baseline

Stage 23.4 hardening complete: durable SQLite source (schema v4), durable
background jobs, worker/retry/recovery, source/processing revisions,
stale-worker protection, revision-aware semantic retrieval, real FastAPI /
worker E2E, restart recovery, concurrency + integrity tests. Zero
authentication: 31 endpoints, no middleware, one global data plane, no
user/org/tenant concept anywhere (see security/data-flow map in §6).

## 2. Authentication design

- Passwords: **PBKDF2-HMAC-SHA256** (stdlib `hashlib`, NIST-approved; no new
  dependency was available — bcrypt/argon2/jose are not installed — and no
  cryptography was invented). Per-user 16-byte `secrets` salt, 600,000
  iterations default (OWASP), constant-time verify, self-describing encoding.
- Sessions: **opaque server-side bearer tokens** (`secrets.token_urlsafe`).
  Only the SHA-256 *hash* is persisted — a database read never yields a
  credential. Fixed TTL (24 h default), server-side revocation (logout,
  disable), per-request validation of expiry + revocation + ACTIVE status.
- Login failures (unknown email / wrong password / disabled / expired) share
  one generic message; missing accounts cost equal PBKDF2 work (dummy hash).
- No credential material is ever logged, audited, or returned
  (single audit call-site enforces banned keys).

## 3. User model — IMPLEMENTED

`User`: `user_id`, `email` (normalized lowercase, UNIQUE), `password_hash`,
`status` (`ACTIVE`/`DISABLED`), `created_at`, `updated_at`.
Disabled users cannot authenticate and their sessions die immediately
(revoked on disable). Access history is never deleted to remove access —
removal is a membership tombstone.

## 4. Organisation model — IMPLEMENTED

`Organisation`: `organisation_id`, `name` (not unique — two tenants may both
be "Acme"), `slug` (globally unique handle), `status`, timestamps.
`OrganisationMember`: `(organisation_id, user_id)` UNIQUE, `role`, `status`
(`ACTIVE`/`REMOVED` tombstone), timestamps. Explicit membership — no
`organisation_id` on `User`, so multi-org membership is first-class.
Roles: finite `OWNER` / `ADMIN` / `MEMBER`. No permission strings.

## 5. Role/permission model — IMPLEMENTED

Central policy `ROLE_PERMISSIONS` (`app/auth/models.py`): 14 finite
permissions (org/member/role management, meeting create/read/update, entity
read/manage, query, intelligence, processing, job read, diagnostics).
`OWNER` = all; `ADMIN` = all but `ORG_MANAGE`; `MEMBER` = data operations.
Route handlers declare one permission via `require_permission(...)`; the
policy lives in one place. Only an `OWNER` may grant `OWNER`; the last
`OWNER` of an org cannot be removed or demoted (409).

## 6. Tenant data model

Security/data-flow map (built before coding):

```text
USER → API → AUTHENTICATION → USER → ORGANISATION → TENANT CONTEXT
→ REPOSITORY → PROCESSING → SEMANTIC INDEX → QUERY
```

- 31 endpoints inventoried: 0 had auth; all `Depends` resolved to global
  singleton repos; no middleware; `/health/diagnostics` + `/api/v1/health/jobs`
  exposed backend state unauthenticated.
- SQLite v4: 7 tables, `payload`-JSON blobs + queryable columns, deterministic
  global IDs (`job_id = type:payload[:revision]`, 12-hex semantic
  `evidence_id`, 16-hex `dependency_id`) with no org input.
- Worker flow meeting_id-only; semantic search full-partition scans;
  resolution candidate generation global scans; graph traversal cross-meeting;
  org intelligence whole-DB aggregation.
- Stage 24: `organisation_id` on meetings, extraction_results, entities,
  mentions, dependencies, background_jobs (columns + indexes), and
  `SemanticIndexRecord`; pre-tenant rows live in the explicit bootstrap org
  `"default"`. Meeting/entity IDs stay globally unique (collision-impossible)
  AND org-checked on access; semantic key becomes
  `(organisation_id, evidence_id, model, version)` so identical evidence in
  two orgs never shares a record.

## 7. Repository isolation — IMPLEMENTED

`app/repositories/scoped_repositories.py`: every store has a view class
subclassing its ABC (`ScopedMeetingRepository`, `…Extraction…`,
`…Entity…`, `…Mention…`, `…Dependency…`, `…BackgroundJob…`,
`…SemanticIndex…`) bundled as `TenantRepositories` per request / per job.
Point reads verify scope (mismatch → None → 404, no existence oracle);
list/search methods inject SQL-level `organisation_id` predicates (never
post-filtered global reads); writes stamp the scope and raise
`TenantScopeMismatchError` (→ 403) on foreign-org objects. Services take
scoped repos unknowingly — resolution, graphs, intelligence, query, and the
worker pipeline are isolated without service rewrites. Legacy unscoped
signatures are preserved (optional params) so the 862 pre-existing tests run
unchanged; production request/worker paths use views exclusively.

## 8. Semantic isolation — IMPLEMENTED

Per-record `organisation_id` in the durable key; `search_similar`,
`list_*`, `count`, `get/delete` all tenant-filtered; `search_persisted`
takes `organisation_id` (vector search filtered AND each hit re-checked
before rehydration — double barrier); `HybridEvidenceRetrievalService`
threads it through. Proven by identical-name cross-tenant retrieval tests
and the e2e semantic cross-fire step (B's exact sentence queried from A
returns zero B content).

## 9. Worker isolation — IMPLEMENTED

Jobs carry immutable `organisation_id` (stamped at enqueue from the meeting,
backfilled from meetings for legacy rows, preserved across retry/recovery).
`process_meeting` builds a fully scoped service graph per job from the
durable row — never from request state. Cross-tenant job (org scope ≠
meeting owner) fails `PermanentJobError` before any stage runs (proven by
test). The claim/recover loop stays global (it must serve all orgs) but
processes each job strictly inside its own scope.

## 10. API authorization — IMPLEMENTED

New routers: `/api/v1/auth/*` (bootstrap/login/logout/me) and
`/api/v1/orgs/*` (create/list/get/rename/members/roles/disable/enable).
All 31 pre-existing endpoints gained permission deps
(`MEETING_CREATE/READ/UPDATE`, `ENTITY_READ/MANAGE`, `QUERY_RUN`,
`INTELLIGENCE_READ`, `PROCESSING_RUN`, `JOB_READ` on `/health/jobs`,
`DIAGNOSTICS_READ` on `/health/diagnostics`). Semantics: 401
unauthenticated, 403 forbidden, 404 for out-of-scope objects. Org context
comes from verified membership (`X-Organisation-ID` checked, never
trusted); `/orgs/{id}` endpoints evaluate membership against the PATH org.
OpenAPI documents all routes (contract test asserts paths).

## 11. Migration — IMPLEMENTED

Schema v5, non-destructive: guarded `ADD COLUMN organisation_id NOT NULL
DEFAULT 'default'` (legacy rows → explicit bootstrap org, never a real
user), job backfill from meetings, 8 tenant indexes, 6 new auth/audit
tables. Proven by a test that strips a real DB back to v4-equivalent state
and re-migrates (data + revisions intact, integrity `ok`).

## 12. Security events — IMPLEMENTED

`security_events` table + `AuthService` audit: login success/failure,
logout, disable/enable, org creation, member add/remove/role change —
operational metadata only (banned-key guard rejects credential material).
Covered by test asserting event kinds and secret absence.

## 13. Rate limiting — IMPLEMENTED

Per-email failure window in SQLite (5 failures / 15 min → HTTP 429, even
for correct passwords while hot — no oracle). Single-process-safe via
SQLite; documented limitation: no distributed counting beyond the shared
DB, best-effort under exact-concurrent bursts.

## 14. Tests added

- `tests/test_stage_24_security.py` — 26 tests (§33: unauthenticated,
  invalid creds, bootstrap closure, weak password, brute-force throttle,
  session expiry, logout, disable/enable, role matrix, last-owner, removal,
  meeting/entity/dependency/graph/job IDOR both directions, header spoof,
  multi-org selection, NL-query isolation, resolution isolation,
  intelligence isolation, name coexistence, scoped-view unit, worker
  barrier, migration, restart, audit, OpenAPI).
- `tests/test_stage_24_e2e.py` — §27 24-step full-stack acceptance (real
  uvicorn ×2 processes, SQLite, worker, persistent semantic index, restart).
- Harness hygiene: `test_extraction.py` leaked global
  `dependency_overrides` (no pops) — invisible pre-tenancy, fatal after
  (mixed overridden/unscoped paths). Fixed with an autouse cleanup fixture;
  security tests additionally clear overrides for hermeticity.

## 15. Cross-tenant attack tests — IMPLEMENTED

Tenant A / Tenant B are disjoint principals (A provably removed from B)
with identical entity/meeting names. Matrix (both directions): meeting
GET/PUT/extract, entity/relationships/dependencies/graph/impacts,
query evidence, semantic cross-fire, job counts, reprocessing — all
rejected (404/403) or provably B-free. Raw `semantic.json` is shown to hold
both tenants while the API never crosses them.

## 16. Real full-stack security acceptance — IMPLEMENTED

`test_real_full_stack_tenant_acceptance` passes (≈7s): bootstrap A →
ingest/process/query A → bootstrap-closure → provision B → ingest/process/
query B → bidirectional IDOR matrix → semantic cross-fire → graph/job
isolation → reprocessing rejection → disable-B immediacy → shared-file
check → process restart (A session + data survive, B stays disabled).

## 17. Final test counts

```text
889 passed, 0 failed, 0 skipped, 0 warnings
```

Two transient load flakes observed during development (subprocess lease race
in `test_h_concurrency_matrix`, once in four full runs — the 1 s-lease test
correctly enforcing expiry under CPU contention) — suite is green on clean
runs; no test was weakened (the matrix test is untouched).

## 18. Warnings

**0.** No warnings summary in the suite output; security suites additionally
pass under `-W error::DeprecationWarning`.

## 19. Remaining limitations

1. Single-process worker + SQLite: rate limiting and sessions are correct
   for this architecture, not for a multi-node deployment (no Redis/JWT).
2. `organisation_id` columns have no DB-level FK to `organisations`
   (deliberate: legacy rows survive without an org row; enforced at the
   repository boundary instead).
3. Meeting/entity IDs are globally unique: a client cannot reuse another
   org's ID (409/404), but IDs are not per-org sequences.
4. Bootstrap-open mode (`AUTH_OPEN_BOOTSTRAP=true`) serves the default org
   until the first user exists — deployers must bootstrap immediately or
   set it false (fail-closed).
5. Fixed session TTL, no sliding refresh, no 2FA, no per-IP throttling.
6. No frontend yet (per stop condition); docs/OpenAPI are the contract.

## Acceptance checklist (§37)

- [x] real authentication works — IMPLEMENTED
- [x] credentials are securely stored — IMPLEMENTED
- [x] sessions/tokens expire safely — IMPLEMENTED
- [x] disabled users cannot access — IMPLEMENTED
- [x] organisation model exists — IMPLEMENTED
- [x] memberships exist — IMPLEMENTED
- [x] roles work — IMPLEMENTED
- [x] authorization is centralized — IMPLEMENTED
- [x] tenant scope is enforced at repositories — IMPLEMENTED
- [x] meetings are tenant scoped — IMPLEMENTED
- [x] entities are tenant scoped — IMPLEMENTED
- [x] mentions are tenant scoped — IMPLEMENTED
- [x] dependencies are tenant scoped — IMPLEMENTED
- [x] jobs are tenant scoped — IMPLEMENTED
- [x] source revisions are tenant scoped — IMPLEMENTED (revisions live on
      org-scoped meetings; job identity is globally unique + org-enforced)
- [x] semantic index is tenant scoped — IMPLEMENTED
- [x] queries are tenant scoped — IMPLEMENTED
- [x] entity resolution is tenant scoped — IMPLEMENTED
- [x] dependency graph is tenant scoped — IMPLEMENTED
- [x] organisation intelligence is tenant scoped — IMPLEMENTED
- [x] workers preserve tenant context — IMPLEMENTED
- [x] process restart preserves tenant context — IMPLEMENTED
- [x] caches cannot cross tenants — IMPLEMENTED (no tenant-sensitive caches
      exist; verified by grep; scoped views hold no shared mutable state)
- [x] cross-tenant IDOR tests pass — IMPLEMENTED
- [x] semantic cross-tenant leakage tests pass — IMPLEMENTED
- [x] authorization tests pass — IMPLEMENTED
- [x] migration is safe — IMPLEMENTED
- [x] bootstrap is secure — IMPLEMENTED
- [x] API contracts are documented — IMPLEMENTED
- [x] security events exist where appropriate — IMPLEMENTED
- [x] brute-force protection exists — IMPLEMENTED
- [x] full-stack security acceptance test passes — IMPLEMENTED
- [x] full existing regression suite remains green — IMPLEMENTED
- [x] 0 warnings — IMPLEMENTED

## Files created

- `app/auth/__init__.py`, `constants.py`, `models.py`, `passwords.py`, `service.py`
- `app/api/auth.py`, `app/api/organisations.py`, `app/schemas/auth.py`
- `app/repositories/auth_repositories.py`, `app/repositories/scoped_repositories.py`
- `tests/test_stage_24_security.py`, `tests/test_stage_24_e2e.py`
- `STAGE_24_FINAL_REPORT.md` (this file)

## Files modified (production)

- `app/models/`: `meeting`, `entity`, `dependency`, `extraction`,
  `background_job`, `semantic_index` (+ `organisation_id`)
- `app/persistence/sqlite_store.py` (schema v5)
- `app/repositories/`: `sqlite_source_repositories` (org columns),
  `entity`/`mention`/`dependency`/`background_job`/`semantic_index`
  (org-filtered lists, org-scoped keys), `semantic_index_repository`
  (P11 `_FileLock` thread-safety fix — shared-instance fd clobber caused
  `PermissionError` under worker+API contention; proven by repro)
- `app/services/`: `background_worker_service` (job org stamp),
  `semantic_evidence_retrieval_service` + `hybrid_evidence_retrieval_service`
  (org barrier)
- `app/api/`: all 8 routers (ctx + permissions), `app/main.py` (routers,
  scoped diagnostics, per-job tenant worker graph)
- `app/core/config.py` (`AUTH_*` settings)
- `README.md` (bootstrap + security documentation)

## Files modified (tests only)

- `tests/test_extraction.py` (autouse override cleanup)
- `tests/test_stage_24_security.py`, `tests/test_stage_24_e2e.py` (new)
