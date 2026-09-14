# Stage 26 Final Report: Real Dashboard / Organisation Home

## Result

The dashboard is now a real, data-driven organisation home: attention,
portfolio, recent changes, and job-queue health all come from live backend
APIs — no mock or invented data anywhere. Sections load in parallel,
degrade independently, and every row navigates to a real entity/meeting
route. This stage establishes the product-quality pattern for later
feature screens.

```text
Frontend: npx tsc --noEmit            → clean (EXIT 0)
          npm run build               → production build succeeds (code-split)
          npm test (vitest run)       → 11 files, 67/67 passed (was 52)
Backend:  891 passed, 0 failed        → full `python -m pytest` (green, +2 contract tests)
          live uvicorn smoke (HTTP)   → attention / portfolio / changes / jobs all 200
          tests/test_frontend_contracts.py → 2 passed (live round-trip contracts)
```

## What was built

### Data layer (`src/features/dashboard/useDashboard.ts`)

One hook per dashboard question, each backed by a real endpoint and a
tenant-scoped key (`["tl", organisationId, ...]`):

- `useDashboardAttention()` → `GET /api/v1/attention`
- `useDashboardPortfolio()` → `GET /api/v1/portfolio`
- `useDashboardChanges()` → `GET /api/v1/changes?limit=8`
- `useDashboardJobHealth()` → `GET /api/v1/health/jobs` (auto-refreshes every 30s)
- `useEntityDirectory()` → entity names joined from the portfolio (no N+1)
- `useDashboardMeetingTitles(ids)` → bounded fan-out (≤5) under the
  standard per-meeting tenant keys, so results share the meeting cache.

All five queries run in parallel — no waterfalls — and fail independently so
one backend error degrades a single section instead of the whole page.

### Section components (`src/features/dashboard/DashboardSections.tsx`)

`AttentionSection`, `ChangesSection`, `ProcessingSection`, `RisksSection`,
`ExploreSection`. Each owns one backend question and renders three honest
states: structural skeleton (loading), `ErrorState` with retry (failure),
and calm empty copy (no data). No section invents numbers; the processing
strip and risk rows are composed from the API's real counts/levels.

### Page (`src/features/dashboard/DashboardPage.tsx`)

Greeting from the signed-in user's email, aggregate snapshot line from real
portfolio/attention counts, 2-column grid (attention + changes), a live
processing strip, risks & blockers with impact/action framing, and an
explore card set. Primary action links to the real `/app/meetings`.

### Formatting (`src/features/dashboard/dashboardFormat.ts`)

Pure helpers: time-of-day greeting, email→display name, relative time,
duration, ISO datetime, backend vocabulary labels (attention reasons,
change types, lifecycle states) with safe fallbacks, aggregate snapshot
line, and last-resort id shortening.

### Styles (`src/features/dashboard/dashboard.css`)

Responsive grid (stacks under 900px), rows, status dot, evidence clamp,
explore cards, fresh design tokens. A11y: heading hierarchy per section,
`<ul>` rows, `role="status"` for the processing strip, hidden decorative
icons.

## Bugs found and fixed during the stage

1. **Org-switch request race (cross-tenant correctness)**: after switching
   organisation, `queryClient.clear()` retriggered dashboard refetches that
   fired with a **null** `X-Organisation-ID` header. Root cause: the org
   reader was assigned in a `[selectedId]` passive effect whose cleanup set
   the ref to `null`; cache-cleared refetches can run in that window. Fixed
   by installing the getter once (it reads a mirror ref updated
   synchronously in `select()` and on render) — swaps no longer drop tenant
   scope, and the tenant-switch test now asserts scoped requests + a fully
   swapped dashboard.
2. **Stale heading assertions**: three pre-existing route tests asserted the
   old placeholder heading; updated to the real greeting/snapshot copy.
3. **`AttentionResponse.evaluated_at` doesn't exist** (TypeScript): the
   freshness stamp now uses portfolio + changes evaluated_at.
4. **Skeleton a11y**: section skeletons were `aria-hidden`, so the loading
   test couldn't observe them as `role="status"`; now announced.

## Files created

- `frontend/src/features/dashboard/useDashboard.ts` — data hooks
- `frontend/src/features/dashboard/DashboardSections.tsx` — section components
- `frontend/src/features/dashboard/dashboardFormat.ts` — formatting helpers
- `frontend/src/features/dashboard/dashboard.css` — dashboard styles
- `frontend/src/features/dashboard/dashboard.test.tsx` — 8 integration tests
  (real contract shapes, loading, empty, partial failure, queue honesty,
  member permissions, tenant-A≠tenant-B switch)
- `frontend/src/features/dashboard/dashboardFormat.test.ts` — 6 unit tests

## Files modified

- `frontend/src/features/dashboard/DashboardPage.tsx` — real dashboard page
- `frontend/src/types/intelligence.ts` — `OrganisationChange` extended with
  `insight_id`, `dependency_path`, `impact_count`, `related_entity_ids`
  (verified against `app/schemas/changes.py`)
- `frontend/src/auth/OrganisationContext.tsx` — zero-null-window org reader
- `frontend/src/api/keys.test.ts` — new dashboard key assertions
- `frontend/src/auth/guards.test.tsx`, `organisation-switching.test.tsx`,
  `features/auth/LoginPage.test.tsx` — real greeting/snapshot assertions
- `tests/test_frontend_contracts.py` — added live dashboard contract test
  (attention / portfolio / changes?limit=8 / health/jobs exact envelopes)

## Notes / limitations

- **Visual review**: no headless-browser tooling exists in this repo, so the
  responsive behaviour was verified at the DOM/unit level (grid + stacking
  breakpoints in CSS) rather than in a live browser at 1440/1280/768/390.
  A real-browser pass is recommended before the next stage if one is wanted.
- `AGENTS.md` references `tests/test_security_contracts.py` and
  `tests/quick_contracts.py`, which do not exist (stale names); the real
  equivalents are `tests/test_stage_24_security.py` (+ the contract test).
  Suggested follow-up: update those shortcuts.
- The live uvicorn smoke test confirmed all four dashboard endpoints respond
  200 with correct envelopes over real HTTP (temp script, outside the repo).
- Portfolio excludes zero-observation entities by design (backend rule), so
  a freshly created entity only appears after it is observed.

## Acceptance checklist

- [x] Dashboard reads attention / portfolio / changes / job health from real APIs
- [x] No mock or invented data anywhere in production code
- [x] Sections load in parallel and degrade independently (loading/error/empty)
- [x] Every row navigates to a real entity or meeting route
- [x] Tenant switching swaps dashboard data and keeps requests scoped
- [x] Processing strip reflects real queue state; worker-off handled honestly
- [x] Responsive grid + consistent design tokens; a11y (headings, lists, status)
- [x] Frontend: typecheck clean, build succeeds, 67/67 tests pass
- [x] Backend: full suite green (891); live contract + uvicorn smoke pass
- [x] `STAGE_26_FINAL_REPORT.md` written