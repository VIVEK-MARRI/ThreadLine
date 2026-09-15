# Stage 28 Final Report: Entity Intelligence Workspace

## 1. Backend endpoints inspected

All entity intelligence calls use real, existing FastAPI contracts. No backend
source code was changed.

Inspected and used:

- `GET /api/v1/entities`
- `GET /api/v1/entities/{entity_id}`
- `POST /api/v1/entities`
- `GET /api/v1/entities/{entity_id}/temporal`
- `GET /api/v1/entities/{entity_id}/timeline`
- `GET /api/v1/entities/{entity_id}/memory`
- `GET /api/v1/entities/{entity_id}/insights`
- `GET /api/v1/entities/{entity_id}/attention`
- `GET /api/v1/entities/{entity_id}/actions`
- `GET /api/v1/entities/{entity_id}/relationships`
- `GET /api/v1/entities/{entity_id}/dependencies`
- `GET /api/v1/entities/{entity_id}/dependency-graph`
- `GET /api/v1/entities/{entity_id}/impacts`
- `GET /api/v1/portfolio`
- `GET /api/v1/changes?entity_id=&limit=`

Supporting contracts inspected but intentionally not called directly where a
more appropriate entity-scoped endpoint already supplied the data:

- Correlations: temporal observations already provide meeting titles, dates,
  evidence, and mention IDs.
- Relationship graph: supplies the explicit edges and associations needed by
  the workspace, avoiding a second dependency-only fetch.
- Meeting detail: related-meeting titles come from temporal observations, so
  the detail page never performs one meeting lookup per related meeting.

The entity list supports backend filtering only by `entity_type`. It does not
expose search, lifecycle-state, attention, or sort query parameters. The
portfolio exposes organisation-wide intelligence but no per-request filters.
Those limits shaped the directory design rather than being worked around with
invented semantics.

## 2. Entity information architecture

The workspace follows the requested ThreadLine progression:

```text
Entity
→ current state
→ history
→ risks
→ dependencies
→ impact
→ meetings
→ changes
→ memory/evidence
→ actions
```

The detail layout therefore renders, in order:

1. Identity header
2. Current state and current attention
3. Unified timeline
4. Risk and unresolved signals
5. Explicit dependencies, dependency paths, and observed associations
6. Potential impact
7. Related meetings
8. Changes
9. Memory and evidence
10. Attention and recommended actions
11. Key-context metadata rail

No section invents ownership, descriptions, probabilities, summaries, counts,
timestamps, dependency reasons, or confidence scores.

## 3. Entity list design

`/app/entities` is a real directory combining:

- the canonical entity registry: name, type, aliases, creation date; and
- the organisation portfolio snapshot: current state, attention level and
  score, observations, actions, and impacts.

The backend `type` control calls `GET /entities?entity_type=...`. Search,
lifecycle-state, and sort controls are explicitly presentation-layer controls
over the loaded tenant-scoped directory. Search is debounced and all three
controls plus the query are preserved in shareable URL parameters.

The result line reports both dimensions honestly, for example how many loaded
entities are shown and how many have an assessed intelligence record. If the
portfolio is temporarily unavailable, the registry remains usable and the page
says intelligence is temporarily unavailable instead of inventing state or
attention.

## 4. Entity detail architecture

`/app/entities/:entityId` is implemented as a signature editorial page:

- `EntityDetailPage.tsx` owns only core entity loading, loading layout,
  error handling, breadcrumbs, title, and metadata.
- `EntityDetailSections.tsx` owns one backend question per section.
- `useEntities.ts` owns tenant-scoped fetching and bounded name joins.
- `entitiesFormat.ts` owns vocabulary mappings, sorting, evidence selection,
  meeting selection, and dates.
- `entities.css` supplies the workspace’s responsive editorial layout while
  reusing global ThreadLine tokens.

Each section renders:

- structural skeleton while loading;
- `ErrorState` with retry on failure;
- intentional empty copy when the backend has no applicable records;
- real lists, badges, quotes, timestamps, and navigation when data exists.

No section blocks another section.

## 5. Timeline implementation

The timeline uses `GET /entities/{id}/timeline`, the backend’s unified
chronological aggregation of:

- observations;
- state changes;
- memory milestones;
- insights;
- attention snapshots; and
- recommended actions.

The UI preserves backend order and displays it newest-first with an explicit
caption. Long histories initially show the latest twelve events and can
expand to the full deterministic sequence. Each event shows its backend
title, description, timestamp, category, and meeting link when the event has
a `related_meeting_id`.

Meeting titles come from the temporal endpoint, so timeline links remain
real without issuing a meeting request per event.

## 6. Risk implementation

Risks use `GET /entities/{id}/insights` and display only `WARNING` and
`CRITICAL` insights.

Each risk shows:

- backend title and description;
- severity with text plus tone;
- observation timestamp;
- exact backend evidence;
- source-meeting link when available.

Informational state changes remain history; they are not relabelled as
risks. When no warning or critical insight exists, the page says there are
no active risk signals.

## 7. Dependency implementation

Dependencies combine two real sources:

- `GET /entities/{id}/relationships` for direction-aware explicit edges and
  observed associations; and
- `GET /entities/{id}/dependency-graph?max_depth=3` for outgoing direct and
  transitive paths.

The UI distinguishes:

- “This entity depends on”;
- “Required by”;
- explicit “Blocks” / “Blocked by” evidence;
- dependency paths with depth, entity sequence, relationship vocabulary, and
  cycle disclosure; and
- “Observed with” associations.

Observed co-occurrence is never promoted to a dependency or causal claim.
Every linked entity navigates to `/app/entities/:entityId`.

## 8. Impact implementation

Impact uses `GET /entities/{id}/impacts?max_depth=3`.

Each impact shows:

- source entity;
- exact impact level;
- backend reason;
- relationship strength;
- risk-signal vocabulary;
- evaluation timestamp.

The section explicitly says association is not causation. Backend does not
provide probability percentages, so none are shown.

## 9. Related meetings

Related meetings use the temporal endpoint’s resolved observations.

The workspace:

- deduplicates meetings by ID;
- counts exact observations per meeting;
- orders meetings newest-first;
- shows the newest eight;
- reports the total associated-meeting count; and
- links each row to the real meeting route.

No per-meeting `GET /meetings/{id}` call is made. A dedicated test asserts
that the frontend never issues those point lookups for related meetings.

## 10. Changes

Changes use `GET /changes?entity_id={id}&limit=20`.

Each change shows backend type, severity, evidence, detection timestamp, and
source-meeting link when the meeting is available from temporal history.
Records retain backend severity and change ordering. An empty backend result
produces a precise “no changes attributed” state rather than a generic
no-data message.

## 11. Memory/evidence

Memory uses `GET /entities/{id}/memory`.

Facts are shown with their backend category, value, meeting title where
provided, timestamp, and source-meeting link. `CURRENT_STATE` is explicitly
identified as an organisation-wide aggregate when it has no single source.
Source-mention IDs are joined to temporal evidence text so observation-backed
facts can show their exact transcript excerpt.

Missing non-aggregate dates say “Date not provided.” No fabricated date or
summary is used.

## 12. Contextual attention/actions

Current attention also appears in the current-state area when the backend
reports it. The dedicated bottom section uses:

- `GET /entities/{id}/attention`; and
- `GET /entities/{id}/actions`.

Attention displays exact level, numeric score, reason vocabulary, and
evaluation time. Actions display exact recommended text, type, priority,
reason, creation timestamp, and source meeting. Empty backend responses
produce calm “no attention signals” and “no recommended actions” states.

The workspace does not duplicate a full action center; it presents only
entity-contextual backend recommendations.

## 13. API/data layer

`frontend/src/api/entities.ts` now exposes the inspected routes:

- `relationships`
- `temporal`
- `unifiedTimeline`
- `memory`
- `correlations`
- `insights`
- `attention`
- `actions`
- `dependencyGraph`
- `impacts`

Existing `list`, `get`, `dependencies`, `create`, and `registerMention`
behavior is unchanged. No endpoint, field, filter, or vocabulary was
invented.

## 14. Query/cache strategy

All workspace queries use the existing `["tl", organisationId, ...]`
invariant:

- canonical entity detail;
- every entity sub-resource;
- entity-scoped changes;
- organisation portfolio;
- bounded entity-name joins.

Sections fetch in parallel. Bounded name resolution uses the same
per-entity tenant keys as other workspace code, allowing caches to be
shared rather than refetched per section. Recording an entity invalidates
the tenant’s entity namespace and portfolio snapshot.

Related data is joined from already-loaded backend responses wherever
possible. Meeting titles come from temporal data, source-meeting titles come
from temporal/memory data, and entity names are bounded rather than fetched
without limit.

## 15. Tenant handling

Organisation switches change every query namespace. Tenant A cannot reuse
cached Tenant B records because all cache entries contain the organisation
segment.

Integration coverage includes:

- same-named entities returning different state after an organisation
  switch in the directory;
- a full detail workspace changing timeline, insights, dependencies,
  meetings, memory, and actions after an organisation switch;
- exact route-level 404 and 403 responses sharing one safe “unavailable”
  experience;
- a live backend acceptance test verifying all workspace routes and
  unknown-organisation rejection.

## 16. Permissions

Backend policy remains authoritative. All authenticated OWNER, ADMIN, and
MEMBER roles currently hold entity read and entity management rights.

The frontend adds tested role hints:

- `canReadEntity`
- `canRecordEntity`

Entity recording remains visible to authenticated roles because the backend
grants those roles `ENTITY_MANAGE`. Permissions never gate fetching and are
never treated as security boundaries.

## 17. Responsive behavior

Desktop uses the established main-plus-rail workspace grid:

```text
main content | 20rem context rail
```

Tablet and mobile collapse to one editorial column in the product order:

1. identity;
2. current state;
3. attention/risk;
4. dependencies;
5. timeline;
6. meetings;
7. changes;
8. memory;
9. actions; and
10. key context.

Tables use the existing horizontally scrollable table treatment instead of
forcing a desktop grid onto narrow screens. Long associations and timelines
use bounded displays plus explicit expansion controls.

## 18. Accessibility

The implementation preserves Stage 25 accessibility standards:

- one `h1` per page;
- semantic `h2` sections and `h3` dependency subsections;
- real links for navigation;
- real buttons for tabs, expansion, retry, and form actions;
- accessible table caption;
- labelled search, selects, and creation fields;
- skeleton and result-count `role="status"` announcements;
- text-paired status badges rather than color-only meaning;
- numbered timeline list;
- keyboard-operable details and controls;
- no interactive `div` controls.

## 19. Tests

### Directory: `entities.test.tsx` — 6 tests

Covers:

1. structural loading;
2. registry/portfolio success;
3. backend type filtering;
4. locally applied state and sort controls;
5. debounced search and URL state;
6. recording and tenant-aware refresh;
7. registry and intelligence failures handled separately;
8. same-named tenant switching.

### Detail: `entityDetail.test.tsx` — 8 tests

Covers:

1. structural loading;
2. full current-state/history/risk/dependency/impact/meeting/change/memory/action rendering;
3. timeline expansion and source connectivity;
4. one failed intelligence endpoint without losing other sections;
5. missing and forbidden entities;
6. full tenant switching;
7. directory → entity → related entity → meeting navigation;
8. absence of per-meeting point lookups for related meetings.

### Supporting tests

- `entitiesFormat.test.ts` — 7 pure-function tests for controls, directory
  combination, vocabulary mappings, meeting selection, and signal selection.
- `api/keys.test.ts` — tenant namespacing and isolation for entity sections.
- `auth/permissions.test.ts` — entity read/manage role hints.
- `tests/test_frontend_contracts.py` — one new live backend acceptance test.

All required test categories are covered:

1. entity list loading;
2. entity list success;
3. entity list empty;
4. entity list error;
5. entity search;
6. entity detail loading;
7. entity detail success;
8. current-state rendering;
9. timeline rendering;
10. risk rendering;
11. dependency rendering;
12. impact rendering;
13. meeting rendering;
14. changes rendering;
15. memory/evidence rendering;
16. section-level partial failure;
17. entity not-found;
18. tenant switching;
19. tenant query-key isolation;
20. navigation entity → meeting;
21. navigation entity → related entity;
22. permission handling.

## 20. Full-stack acceptance

The new backend contract test creates a real organisation, user, meeting,
entity, and resolved mention, then exercises every entity-workspace read
endpoint:

- detail;
- temporal lifecycle;
- unified timeline;
- memory;
- insights;
- attention;
- actions;
- relationships;
- dependencies;
- dependency graph;
- impacts;
- entity-filtered changes;
- portfolio inclusion;
- missing-entity 404; and
- unknown-organisation 403.

It also verifies the created observation and meeting appear in temporal and
memory data, type filtering includes/excludes the entity correctly, and the
mention resolves to the created canonical entity.

Frontend integration tests separately verify navigation, full tenant
switching, and safe handling of missing and forbidden entities.

## 21. Visual QA

No headless-browser tooling is available in the repository, so visual QA was
performed through the strongest available DOM/CSS verification:

- verified one heading hierarchy per page;
- verified skeleton-to-content structure for every major section;
- verified empty, error, and partial-failure states;
- verified editorial stacking order in source/DOM order;
- verified responsive breakpoints and scrollable-table CSS behavior;
- verified status text accompanies every status tone;
- verified no new colors, radii, shadows, fonts, or decorative effects;
- verified constrained disclosure controls for long timelines and
  associations;
- verified production build emits the new code-split entity chunks;
- verified link destinations for entities, meetings, and evidence sources.

A real-browser pass at wide desktop, normal desktop, tablet, and mobile
remains advisable before release if one is wanted.

## 22. Build/typecheck results

```text
npx tsc --noEmit
→ clean, no output

npm test
→ Test Files: 18 passed
→ Tests: 111 passed
→ Duration: 26.80s

npm run build
→ production build succeeded in 457ms
→ EntityDetailPage chunk: 26.39 kB / 5.57 kB gzip
→ EntitiesPage chunk: 7.44 kB / 2.90 kB gzip

python -m pytest -q
→ 896 passed in 77.95s

python -m pytest tests/test_frontend_contracts.py tests/test_stage_24_security.py -q
→ 30 passed in 20.56s
```

No backend source files were changed. Backend verification nevertheless ran
the complete suite because a new contract test was added.

## 23. Files changed

Created:

- `frontend/src/features/entities/EntityDetailSections.tsx`
- `frontend/src/features/entities/entities.css`
- `frontend/src/features/entities/entities.test.tsx`
- `frontend/src/features/entities/entitiesFormat.test.ts`
- `frontend/src/features/entities/entitiesFormat.ts`
- `frontend/src/features/entities/entityDetail.test.tsx`
- `frontend/src/features/entities/useEntities.ts`

Modified:

- `frontend/src/features/entities/EntitiesPage.tsx`
- `frontend/src/features/entities/EntityDetailPage.tsx`
- `frontend/src/types/entities.ts`
- `frontend/src/api/entities.ts`
- `frontend/src/api/keys.test.ts`
- `frontend/src/auth/permissions.ts`
- `frontend/src/auth/permissions.test.ts`
- `tests/test_frontend_contracts.py`

Routes were already present:

- `/app/entities`
- `/app/entities/:entityId`

## 24. Remaining limitations

- The backend does not expose directory-level search, state, attention, or
  sort parameters, so those directory controls narrow the loaded
  tenant-scoped dataset. Type filtering is the only server-side directory
  filter.
- Last-activity dates are available on the detail page from memory, but no
  portfolio/list field exposes a per-entity last-activity timestamp, so the
  directory uses exact observation/action/impact counts instead.
- The backend supports only `PERSON` and `ISSUE` entity types. The workspace
  therefore does not display project, team, system, service, product, or
  initiative as separate backend types.
- Related meetings are bounded to the newest eight associations; the total
  association count remains visible.
- Long associations, dependency paths, and impacts use bounded initial
  displays to avoid unbounded DOM growth.
- Visual QA was DOM/CSS-level because the repository has no browser-testing
  infrastructure.
