# Stage 27 Final Report: Meetings Workspace — Live List & Meeting Detail

## Result

Meetings are now a real workspace backed entirely by live backend APIs:
the list page queries real records with search, status filtering,
sorting, and pagination; the detail page shows the stored source,
processing lifecycle, extracted facts (decisions/tasks/issues/risks),
linked people and entities, explicit dependencies, and related
organisation changes — every read tenant-scoped, every section degrading
independently. No mock or invented data anywhere.

```text
Frontend: npx tsc --noEmit            → clean (EXIT 0)
          npm run build               → production build succeeds (code-split)
          npm test (vitest run)       → 15 files, 89/89 passed (was 67)
Backend:  895 passed, 0 failed        → full `python -m pytest` (green, +4 tests)
          tests/test_frontend_contracts.py → 2 passed (live round-trip contracts)
```

## What was built

### Backend endpoints (`app/api/meetings.py`)

- `GET /api/v1/meetings?limit=&source=&status=&q=&sort=` — real list with
  search (title/transcript), status filter, and oldest/newest/relevance
  sorting, all scoped to the requesting organisation.
- `GET /api/v1/meetings/{id}` — detail.
- `GET /api/v1/meetings/{id}/extraction` — stored extraction or explicit
  `has_extraction: false`.
- `GET /api/v1/meetings/{id}/processing` — durable source/pipeline state
  (`source_revision`, `extraction_revision`, `derived_revision`,
  `semantic_revision`, `stale_mentions`, `worker_enabled`).
- `GET /api/v1/meetings/{id}/mentions` — resolved/unresolved entity
  mentions.
- `POST /api/v1/meetings/{id}/extract` — explicit processing refresh.
- `GET /api/v1/changes?meeting_id=` — changes filtered to one meeting.

Schemas in `app/schemas/meeting.py`; read paths via
`meeting_repository` (InMemory/SQLite) + `scoped_repositories`.
Security/tenant tests extended in `tests/test_stage_24_security.py`.

### Data layer (`frontend/src/features/meetings/useMeetings.ts`)

One hook per real question, each with a tenant-scoped key:

- `useMeetingList(limit)` / `useMeetingDetail(id)` / `useMeetingExtraction`
  / `useMeetingProcessing` / `useMeetingMentions` / `useMeetingChanges`
  (with `meeting_id` filter)
- Bounded name resolution `useMeetingEntityNames` (≤12) and dependency
  resolution `useMeetingDependencies` (≤3 sources) joined from mentions —
  no N+1 fan-outs.
- Processing polls every 5s only while queued work can still resolve.

### Pages

- `MeetingsPage.tsx` — debounced search (`q`), status filter, sort menu,
  limit selector, table (accessible `<caption>` name), empty/error states,
  and working "Record a meeting" form; URL params are the source of truth.
- `MeetingDetailPage.tsx` + `MeetingDetailSections.tsx` — processing badge
  (Complete/Running/Failed/Stale wiring), facts, people & entities,
  dependencies, related changes, transcript details, metadata rail, and an
  explicit "Refresh extraction" action gated by `canRunMeetingProcessing`.

### API / types / formatting / permissions

- `api/meetings.ts` — new `list`/`get`/`getExtraction`/`getProcessing`/
  `listMentions`/`extract` methods; `entities.dependencies`;
  `intelligence.changes` gained the `meeting_id` filter.
- `api/keys.ts` — `meetings(filters)` and `meetingSection` keys.
- `types/meetings.ts`, `types/entities.ts` — full contract types.
- `meetingsFormat.ts` — date/time, status labels/tones, mention ordering,
  transcript size, participant preview, unique entity ids. Unit-tested.
- `auth/permissions.ts` — `canRunMeetingProcessing`.
- `meetings.css` — page grid, table, fact lists, dependency rows, badges.

## Bugs found and fixed during the stage

1. **Guard-triggered remount round-trip (intermittent list-test
   failures)**: `OrganisationContext` marked the selection `resolved` while
   `user` was still null, so on the transient render (memberships present,
   `selectedId` not yet committed) `RequireOrganisation` redirected to
   `/app/select-organisation` and back, unmounting the destination page,
   dropping local state, and refetching. Tests that gated on local
   interaction (form submit, sort menu) lost their in-flight state and
   failed intermittently. Fixed by holding `selectionResolved` false until a
   selection is committed for a real user, so the guard shows a brief
   splash instead of redirecting during the transient. Verified with mount /
   navigation instrumentation: the round-trip no longer fires.
2. **Detail-test assertion races**: `meetingDetail.test.tsx` asserted
   extraction facts synchronously after waiting only on the processing
   badge, while extraction/entity/dependency/change queries resolve in
   parallel — a race the old remount happened to mask. Converted the fact
   assertions to a single `waitFor`.
3. **Multi-match queries in the detail test**: entity links repeat across
   the people/entities, dependencies, and changes sections, and "depends
   on" appears in both sentence and badge form. Switched the test to
   `getAllByRole`/`getAllByText` so legitimate repetition isn't an error.

## Files created

- `frontend/src/features/meetings/MeetingDetailSections.tsx`
- `frontend/src/features/meetings/MeetingDetailPage.tsx` (rewritten)
- `frontend/src/features/meetings/MeetingsPage.tsx` (rewritten)
- `frontend/src/features/meetings/useMeetings.ts`
- `frontend/src/features/meetings/meetingsFormat.ts` + `meetingsFormat.test.ts`
- `frontend/src/features/meetings/meetings.css`
- `frontend/src/features/meetings/meetings.test.tsx` (7 tests)
- `frontend/src/features/meetings/meetingDetail.test.tsx` (7 tests)
- `frontend/src/test/search-probe.test.tsx` (router search persistence)

## Files modified

- Backend: `app/api/meetings.py`, `app/api/changes.py`,
  `app/schemas/meeting.py`, `app/repositories/meeting_repository.py`,
  `app/repositories/scoped_repositories.py`,
  `app/repositories/sqlite_source_repositories.py`,
  `app/services/meeting_service.py`,
  `app/services/organisation_change_intelligence_service.py`,
  `tests/test_meetings.py`, `tests/test_persistent_meetings.py`,
  `tests/test_changes.py`, `tests/test_stage_24_security.py`
- Frontend: `src/api/meetings.ts`, `src/api/entities.ts`,
  `src/api/intelligence.ts`, `src/api/keys.ts`, `src/api/keys.test.ts`,
  `src/types/meetings.ts`, `src/types/entities.ts`,
  `src/auth/OrganisationContext.tsx`, `src/auth/guards.tsx`,
  `src/auth/permissions.ts`, `src/auth/permissions.test.ts`

## Notes / limitations

- **Visual review**: no headless-browser tooling in this repo; responsive
  behaviour is verified at the DOM/unit level rather than in a live browser.
- The list/detail tests stub fetch against the real backend contracts
  (`src/test/harness.tsx`), so they pin endpoint shapes exercised by the
  backend suite.

## Acceptance checklist

- [x] Meetings list reads live backend records with search/filter/sort/limit
- [x] Meeting detail shows processing, stored facts, entities, dependencies,
      changes, and transcript — all from real APIs
- [x] Extraction refresh is explicit and permission-gated, never used to read
      stored facts
- [x] Every query/request is tenant-scoped; tenant-switch test verifies scope
- [x] Sections load in parallel and degrade independently (loading/error/empty)
- [x] No mock or invented data in production code
- [x] Guard no longer remounts pages mid-interaction (cross-stage regression fixed)
- [x] Frontend: typecheck clean, build succeeds, 89/89 tests pass
- [x] Backend: full suite green (895)
- [x] `STAGE_27_FINAL_REPORT.md` written