# STAGE 34 FINAL REPORT — Hardening + Proactive Intelligence Foundation + Query Cost Protection

## 1. Baseline test results (before coding)

| Suite | Result |
|---|---|
| Backend `python -m pytest -q` | **898 passed** (96.39s) |
| Frontend `npm test` (vitest) | **172 passed**, 24 files (182.63s) |
| Frontend `npx tsc --noEmit` | **clean**, exit 0 |
| Frontend `npm run build` | **green** (1.48s) |
| Playwright E2E `npm run test:e2e` | **26 passed** (1.1m, clean backend booted by Playwright itself) |
| Ruff `ruff check .` (v0.16.7, installed into `.venv` for this stage; no repo config exists) | **1096 findings** |

Working tree was clean at `76cb3a4` (Stage 33 release baseline, pushed to `origin/main`).

## 2. Dead code removed (A1)

Deleted `app/services/evidence_index_service.py` (158 lines, Stage 19 in-memory index superseded by the Stage 21 semantic-index architecture).

Verification before deletion (whole-repo search): zero imports, zero dynamic imports (`importlib`), zero test dependencies. Only references were the file itself and `STAGE_19_FINAL_REPORT.md`, which documents it as a *Stage 19 implementation* (historical record, not an active reference) — left untouched per "update documentation only where necessary". Post-deletion search confirms zero code references; `tests/test_stage_34_part_a.py` guards against reintroduction.

## 3. Annotation bug fixed (A2)

`app/services/semantic_indexing_service.py:53` referenced `Callable` in the `current_revision_lookup` string annotation without importing it. Added `Callable` to the existing `typing` import (consistent with 6 other services; the RU035 `collections.abc` suggestion is intentionally left — §6). Runtime resolution proven (`typing.get_type_hints` returns `Optional[Callable[[str | None], int | None]]`) and locked by 3 dedicated tests.

## 4. Import cleanup (A3/A4)

- `app/main.py`: removed the 6 duplicate import lines (exact re-imports of lines 24–29); verified no duplicates remain.
- Unused imports removed: `EntityType`, `TemporalState` (organisational_memory_service); `re`, `typing.Optional` (query_intent_service); `MAX_EVIDENCE_ITEMS`, `make_query_id` (natural_language_query_service); `fastapi.Depends` (function-level import in `health_diagnostics`).
- No runtime behavior changed (full suite green after).

## 5. Exception-handling changes (Part B)

Spec audit said 15 broad catches; the two named files contain **8** `except Exception:` (5 in `organisation_change_intelligence_service.py`, 3 in `processing_consistency_service.py`) — the discrepancy is recorded here, all 8 were treated.

Evidence-based narrowing (repository contracts checked: domain errors derive from `KeyError`/`ValueError`; `EntityNotFoundError(Exception)`; storage failures surface as `sqlite3.Error`):

| # | Location | Operation | Narrowed to | Fallback preserved |
|---|---|---|---|---|
| 1 | org-change:383 per-entity loop | `_detect_entity_changes` | `(EntityNotFoundError, KeyError, ValueError, TypeError, AttributeError, IndexError)` + rationale comment | skip entity, log with entity id |
| 2 | org-change:801 meeting lookup | `get_by_id` + date read | `(KeyError, ValueError, TypeError, AttributeError)` | skip change (existing debug log fires) |
| 3 | org-change:873 graph build | `build_dependency_graph` | `(EntityNotFoundError, KeyError, ValueError, TypeError, AttributeError)` | skip rule, debug log |
| 4 | org-change:949 impact lookup | `get_entity_impacts` | same as 3 | skip rule, debug log |
| 5 | org-change:1016 transition parse | pure string ops | `(TypeError, AttributeError, IndexError)` | `(None, None)` |
| 6 | consistency:183 SUCCEEDED scan | job history read | `(KeyError, ValueError, TypeError, AttributeError)` + new module logger, debug (meeting id only) | derived=None → INCOMPLETE |
| 7 | consistency:248 lifecycle scan | job list scan | same as 6 | default flags (never CURRENT) |
| 8 | consistency:271 mention scan | mention list + int casts | same as 6 | stale count 0 |
| 9 | main.py:431 diagnostics probe | `semantic.count()` | `(KeyError, ValueError, TypeError, AttributeError)` + comment | `semantic_available=False` |

Deliberate fail-loud cases (documented in code): a mid-scan storage failure now propagates instead of returning a silently partial change list (a total outage already raises at the unguarded `list_entities()` call, so no previously-surviving path was broken); unexpected error types in the per-entity loop and consistency scans propagate. No sensitive data is logged (entity/meeting/job IDs only — never tokens, transcripts, or evidence text). 11 regression tests in `tests/test_stage_34_part_b.py`.

## 6. Lint results before/after (Part C)

- Before: **1096** · After: **1041** (net −55 with all new Stage 34 code included).
- Safe cleanup applied to touched files only (`F401,I001,UP045,UP006,UP012,RUF100,F841`): 48 + 36 autofixes (import sorting, unused imports, `Optional`→`X|None`, unused-noqa/variables). Each batch followed by targeted + full test runs.
- Intentionally left: B008 (158, FastAPI `Depends` patterns — spec-prohibited), BLE001 (deliberate guards), S110, UP045 repo-wide mass modernization (churn), SIM/ISC/C414/G201/DTZ005 style families, UP035 `typing.Callable` (A2 spec + codebase consistency), PLR0124 NaN check (deliberate), SIM102 nested-if (refactor risk), `tests/test_extraction.py:413` invalid noqa (pre-existing, out of scope).

## 7. Bootstrap safety findings (Part D)

No code change needed — the runtime already guarantees the lifecycle: `bootstrap_first_organisation` raises 403 unless durable `user_count()==0` (per call, never cached); `is_bootstrap_open()` = flag AND zero users, fail-closed on store errors; no default credentials exist. Strengthened instead: 8 lifecycle tests (`tests/test_stage_34_part_d.py`: open on fresh install, closes on next call after first owner, second attempt 403, anonymous 401 after owner, closure follows durable state across simulated restarts, flag-off closes, no default creds) and an explicit lifecycle-guarantee paragraph in `DEPLOYMENT.md` §5.

## 8. Proactive intelligence architecture (Part E, E1/E2)

New `ORGANISATION_INTELLIGENCE_SCAN` background operation (`app/services/proactive_intelligence_service.py`). The worker claims the durable job, derives scope **solely from `job.organisation_id`** (re-read from the stored row in the handler; cross-tenant barrier preserved by the existing scoped graph), and invokes the existing services via the tenant graph's new `intelligence` entry (attention, insights, actions, org-changes, portfolio) — no logic copied, no second engine. Signal set = deterministic IDs only (`chg:`/`att:`/`ins:`/`act:`).

## 9. Scheduler architecture (E6/E7)

Single-node recurring tick in `main.py::_maybe_tick_proactive_scheduler`, called from `_worker_loop`: wakes per `PROACTIVE_INTELLIGENCE_INTERVAL_SECONDS`, discovers active orgs via new `list_organisation_ids()` (abstract + in-memory + SQLite auth repos), enqueues one scan per org with no outstanding scan. New settings: `PROACTIVE_INTELLIGENCE_ENABLED=false` (safe default), `PROACTIVE_INTELLIGENCE_INTERVAL_SECONDS=3600.0` (validated > 0; zero/negative rejected). Tick failures log and retry next tick — they can never crash the worker or fabricate results. Not distributed scheduling (documented).

## 10. Durable job behavior (E5)

New `BackgroundJobType.ORGANISATION_INTELLIGENCE_SCAN`; schema v6 migration adds nullable `result_summary TEXT` to `background_jobs` (fresh DBs migrate through the same chain); `BackgroundJob.result_summary` carries the JSON summary; new `record_scan_result()` on all three job-repo implementations (abstract/in-memory/SQLite/scoped-delegate) with checkpoint-style ownership enforcement; `_from_row` tolerates pre-v6 rows. Scheduler uses time-bucketed job IDs (`SCAN:{org}:{bucket}`) so `enqueue`'s ON CONFLICT DO NOTHING dedups within a bucket while history rows accumulate (established "history is preserved" architecture).

## 11. Idempotency design (E3)

Three layers: (1) signal IDs contain no wall-clock (verified: insight/attention/action/change IDs hash entity state only); (2) repeat execution over unchanged state reproduces the identical summary (tested, incl. across different wall-clock times); (3) scheduler dedup via bucketed IDs + outstanding-job check + worker claim/lease. First scan ever establishes the baseline (`new=[]`, never an alert burst).

## 12. Restart/retry behavior

Standard worker semantics apply untouched: handler exceptions → RETRY_WAITING with backoff → FAILED after max attempts (tested, `result_summary` stays None — no fabricated success); stale RUNNING scans `recover_stale()` and rerun identically (tested with a mutable-clock crash simulation); existing good intelligence is read-model state and is never erased by scans.

## 13. Tenant isolation

Proven by construction (scoped repos) and tests: scope-plumbing test (graph built with exactly the durable org), SQLite + in-memory `list_organisation_ids` tests, endpoint tests, and a **live multi-tick proof** (§21).

## 14. Rate limiting design (Part F)

`app/services/rate_limit.py`: process-local sliding-window limiter (per-key timestamp deques, expiry pruning, `max_keys=10000` LRU eviction that fails open, one lock, injectable clock, never logs keys). Wired via `require_query_rate_limit` into `POST /api/v1/query` **and** `/api/v1/query/evidence` (same cost class, shared quota). Key = `user:{user_id}` (anonymous bootstrap callers fall back to client IP). Config: `QUERY_RATE_LIMIT_REQUESTS=60`, `QUERY_RATE_LIMIT_WINDOW_SECONDS=60.0` (both validated positive; limiter rebuilds on settings change for test isolation). Assessment of other endpoints: login keeps its own per-email throttle (untouched, verified independent); meeting ingestion is authenticated, tenant-scoped, and job-backed with bounded retries — left unprotected by decision (documented). 13 tests (unit: allow/deny/expiry/isolation/bounded-memory/4-thread quota exactness/validation; HTTP: under-quota 200s, 429 shape, per-user isolation, window recovery, evidence sharing, login independence).

## 15. Query fallback architecture (Part G)

Boundary in `NaturalLanguageQueryService`: known intent → existing route unchanged; UNKNOWN → optional `AbstractQueryFallbackStrategy.probe()` → non-empty citable evidence continues through the standard steps 4–6 (context bounds, citation validation, provider insufficient flag) with intent preserved as UNKNOWN; empty probe → historical rejection **verbatim**. `LexicalSufficiencyFallback` (`app/services/query_fallback.py`) probes the same bounded, revision-guarded, org-scoped `search_persisted` path the hybrid service uses (top_k ≤ 10; probe failures = insufficient, never a crash) and gates on citability (non-blank ID + entity/meeting attribution) plus ≥2 distinct whole-word question tokens (len ≥ 4) overlapping summary/source text — single common words never suffice; short/vague questions keep the rejection. No score floor invented (scores are uncalibrated rankings); no guessed answers (provider + citation validation still decide). Wired live in `get_natural_language_query_service`. 13 tests; pinned `test_api_query_unknown_intent` passes unchanged with live wiring.

## 16. Live OpenAI integration-test strategy (Part H)

`tests/test_stage_34_part_h.py`: 21 offline contract tests (always run, stubbed SDK clients via the lazy `_client` slot — invocation shape incl. JSON mode/temperature/system+user roles, citation parsing/dedup, insufficient-phrase detection, malformed/empty/schema-mismatch rejections, error propagation per provider, config errors, embedding order/shape/emptiness rules) + 4 live tests gated on `RUN_LIVE_OPENAI_TESTS=true` AND a key (skip cleanly otherwise — verified: 4 skipped by default). Live assertions are structural only.

## 17. Tests added

| File | Tests | Covers |
|---|---|---|
| `test_stage_34_part_a.py` | 7 | A1 deletion guards, A2 annotation resolution |
| `test_stage_34_part_b.py` | 11 | narrowed-exception failure modes |
| `test_stage_34_part_d.py` | 8 | bootstrap lifecycle |
| `test_stage_34_part_e.py` | 31 | migration/plumbing, scanner, scheduler, worker, config, scan-status endpoint |
| `test_stage_34_part_f.py` | 13 | limiter unit + HTTP contract |
| `test_stage_34_part_g.py` | 13 | routing boundary + probe gate |
| `test_stage_34_part_h.py` | 21 + 4 live | provider contracts + opt-in live |
| `scanStatus.test.tsx` | 4 | panel states |
| **Total** | **108 (+4 live)** | minimum was 30 |

Also updated `test_stage_24_security.py` migration test for v6 (pins new version, asserts `result_summary` column rides the 4→6 chain) and extended frontend stubs + section-order list for the new panel.

## 18. Exact test counts (final)

- Backend: **1001 passed, 4 skipped** (baseline 898 + 103 new; skips = unconfigured live tests).
- Frontend: **176 passed** (baseline 172 + 4 new), 25 files.

## 19. Exact typecheck/build results

- `npx tsc --noEmit`: **clean, exit 0**.
- `npm run build`: **green** (`✓ built in 646ms`).

## 20. Exact E2E results

- `npm run test:e2e`: **26 passed** (36.6s; Playwright boots its own backend + preview).

## 21. Clean-environment results

- `scripts/stage32_smoke.py`: **ALL RELEASE SMOKE CHECKS PASSED** (boot, bootstrap, login, worker SUCCEEDED, query, restart durability).
- Live proactive proof (temp server, 3s interval, real services): empty baseline scans recorded; after adding 2 entities + 1 mention, scan recorded **9 signals / 9 new** (`2 act + 1 att + 4 chg + 2 ins`); all following ticks **9 signals / 0 new** — the complete E9 loop verified against durable rows. This exercise caught and fixed a real bug: the portfolio-aggregate signal churned (wall-clock in the snapshot) and was removed — member signals cover portfolio shifts exactly.
- Focused suites: security (`test_stage_24_security.py`) 28 passed; worker/job suites green.

## 22. CI impact

**No CI changes.** Default CI stays network-free (live tests skip without flag+key; `openai` is already in requirements and never imported at test-module level). No live-provider job added: no repository secrets are configured and inventing one is prohibited. Ruff remains a local gate (no repo config; a blocking lint job would turn CI red on 1041 pre-existing findings).

## 23. Documentation changes

- `.env.example`: proactive, rate-limit, and live-test settings (commented, safe defaults).
- `DEPLOYMENT.md`: env table + migration versions 1–6 + bootstrap lifecycle guarantee (§5) + new §§11–13 (proactive ops, rate-limit/fallback incl. single-node limitation, live tests) + 2 troubleshooting rows.
- No README rewrite.

## 24. Known limitations

- Rate limiter is per-process (documented; correct for the single-node deployment, not a distributed guarantee).
- Scan cadence is poll-tick based (fires on 3s-interval multiples in the worst case after restarts — harmless by idempotency).
- Scan history rows accumulate per org per bucket (consistent with the established never-delete-jobs architecture; ~24 rows/org/day at defaults).
- `signal_ids` stored per scan bounded at 5000 IDs, `new_signal_ids` at 200 (deterministic truncation flag).
- Ruff 1041 findings remain (all pre-existing families + new-code minor items; touched files are clean except 3 documented intentional items).
- Live OpenAI path is unexecuted here (no key); covered structurally on skip.

## 25. Explicitly unimplemented future capabilities

Per the brief, NOT built: MCP server, graph database, persisted temporal ledger, distributed scheduler, Redis, Celery, Kafka, Kubernetes, horizontal worker scaling, organisation-wide action management, autonomous agents, uncontrolled chatbot fallback, full semantic/LLM fallback routing (only the boundary + conservative probe), per-item "new" badges on read models (scan-status panel instead), job-history pruning.

## 26. Files changed

New backend: `app/services/proactive_intelligence_service.py`, `app/services/rate_limit.py`, `app/services/query_fallback.py`, `app/api/intelligence.py`, `tests/test_stage_34_part_{a,b,d,e,f,g,h}.py`. Modified backend: `app/main.py` (dedup imports, graph `intelligence` entry, scan handler + registration, scheduler tick, logger), `app/api/query.py` (limiter + fallback wiring), `app/core/config.py` (6 new settings + validators), `app/models/background_job.py` (scan type + `result_summary`), `app/persistence/sqlite_store.py` (v6), `app/repositories/{background_job_repository,auth_repositories,scoped_repositories}.py` (`record_scan_result`, `list_organisation_ids`), `app/services/{semantic_indexing_service,natural_language_query_service,organisation_change_intelligence_service,processing_consistency_service,organisational_memory_service,query_intent_service}.py`, `tests/test_stage_24_security.py` (v6). Deleted: `app/services/evidence_index_service.py`. New frontend: `scanStatus.test.tsx`. Modified frontend: `types/intelligence.ts`, `api/{intelligence,keys}.ts`, `useIntelligence.ts`, `Intelligence{Page,Sections}.tsx`, `intelligence.test.tsx`, `product-journeys.test.tsx`. Docs: `.env.example`, `DEPLOYMENT.md`.

---

**Stop condition respected:** no commit, no push, no tag, Stage 35 not started. Working tree holds the complete Stage 34 implementation, uncommitted, awaiting instruction.
