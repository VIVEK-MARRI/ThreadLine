# Stage 29 Final Report: Organisation Intelligence Workspace

## 1. Backend endpoints inspected

No backend source code was changed. The following existing contracts were
inspected and used:

- `GET /api/v1/attention`
  - Purpose: organisation-wide prioritised attention.
  - Fields used: `entity_id`, `attention_level`, `score`, `reasons`,
    `related_insight_ids`, `evaluated_at`.
  - Rendered as the backend-ordered “Current attention” list.

- `GET /api/v1/portfolio`
  - Purpose: organisation-wide entity risk snapshot and entity directory.
  - Fields used: aggregate counts, `entities[].entity_id`,
    `entities[].canonical_name`, `entities[].entity_type`,
    `entities[].risk_level`, `entities[].attention_level`,
    `entities[].attention_score`, `entities[].impact_count`,
    `entities[].action_count`, `entities[].active_insight_count`,
    `entities[].current_state`, `entities[].observation_count`,
    `evaluated_at`.
  - Rendered as entity names, risk and follow-up context, and freshness.

- `GET /api/v1/changes`
  - Purpose: deterministic organisation changes and filtered intelligence.
  - Server filters used: `severity`, `change_type`, and `limit`.
  - Fields used: `change_id`, `entity_id`, `change_type`, `severity`,
    `detected_at`, `meeting_id`, `source_text`, previous/current state,
    `dependency_path`, `impact_count`, `related_entity_ids`, and `evidence`.
  - Rendered as the filtered change stream, repeated-signal list, impact and
    dependency movement, and chronological recent movement.

- `GET /api/v1/meetings?limit=100`
  - Purpose: one bounded directory of meeting titles and dates.
  - Fields used: `meeting_id`, `title`, and `meeting_date`.
  - Rendered as change and attention investigation links.

Inspected but intentionally not queried directly by this page:

- `GET /api/v1/changes/summary`
  - Organisation aggregates were unnecessary because portfolio aggregates and
    per-record change evidence already answer the workspace questions.

- Entity-specific memory, timeline, insight, attention, action, relationship,
  dependency-graph, and impact endpoints
  - These remain the drill-down sources behind entity links.

- Natural-language query endpoints
  - No question input was needed and no LLM-backed answer is displayed.

- Job-health endpoints
  - Processing health remains owned by the Dashboard; duplicating it here
    would not add intelligence value.

- Organisation-wide action-list endpoints
  - No such read endpoint exists. The page therefore does not fabricate
    recommended-action text.

## 2. Actual contracts used

The page issues six parallel tenant-scoped reads:

1. `GET /api/v1/attention`
2. `GET /api/v1/portfolio`
3. `GET /api/v1/changes`
   - Stream request with backend `severity`, `change_type`, and `limit=50`.
4. `GET /api/v1/changes?change_type=REPEATED_UNRESOLVED&limit=25`
5. `GET /api/v1/changes?limit=100`
   - Independent movement collection for impact and recency views.
6. `GET /api/v1/meetings?limit=100`

Entity type filtering is applied client-side over the loaded change records
using portfolio entity types. Unsupported severity, change-type, or entity-type
URL values are normalised to `ALL` and never sent to the backend.

No exact org-level action list, organisation timeline, organisation memory,
or organisation dependency-edge response was available, so no equivalent
content was invented.

## 3. Intelligence information architecture

Source and DOM order:

1. Intelligence header and freshness
2. Current attention
3. Change stream
4. Repeated signals
5. Dependency and impact movement
6. Recent movement
7. Where follow-up exists

Evidence is not isolated in a separate wall of text. Every attention and
change record has a compact “Why this matters” disclosure containing backend
evidence, source text where provided, and real entity/meeting links.

## 4. Each section and its source endpoint

### Current attention — `GET /attention`

Shows the backend-ordered signals with:

- exact backend level;
- numeric score alongside its contributing backend reasons;
- backend reason vocabulary;
- evaluation timestamp;
- portfolio-resolved entity name; and
- navigation to the entity workspace.

Scores are not reinterpreted as urgency categories.

### Change stream — `GET /changes`

Shows an editorial stream in backend significance order with:

- backend change-type label;
- severity text and tone;
- entity and related-entity links;
- dependency-path names;
- meeting title and link;
- detection timestamp;
- collapsible backend evidence and source text.

Severity and change-type controls use backend query parameters. Entity type
narrows the loaded records through the portfolio directory.

### Repeated signals — `GET /changes?change_type=REPEATED_UNRESOLVED`

Shows only backend-classified repeated observations. No frontend trend
engine was created.

### Dependency and impact movement — `GET /changes?limit=100`

Selects only backend-classified:

- `NEW_DEPENDENCY`;
- `DEPENDENCY_EXPANDED`; and
- `IMPACT_EXPANDED`.

It preserves dependency paths and impact counts without converting
association into dependency or dependency into causation.

### Recent movement — `GET /changes?limit=100`

Shows the newest loaded records first by real `detected_at` values. Records
without usable timestamps remain last. This view is explicitly chronological;
backend significance order remains in the change stream.

### Where follow-up exists — `GET /portfolio`

Lists portfolio entities with `action_count > 0`, preserving risk,
attention, impact, insight, state, and exact action counts. Because no
organisation-level action endpoint exposes recommendation text, type, or
priority, the page links to each entity for its exact recommended actions
instead of inventing them.

## 5. Evidence and navigation connectivity

Supported paths:

```text
Dashboard
→ Intelligence
→ Entity
→ related meeting or related entity
→ source evidence
```

Every change and attention record that has a backend meeting ID links to a
real meeting route. Every known entity links to a real entity route. Related
entities and dependency-path members also link to entity routes.

Meeting titles come from one bounded meeting-list response. Entity names
come from the loaded portfolio response. The page makes no per-signal
entity or meeting request.

When a directory entry is unavailable, links use a shortened identifier as a
support fallback and preserve the correct route.

## 6. Tenant/query-cache strategy

The workspace reuses existing tenant-scoped resource keys rather than
creating a parallel cache namespace:

- `["tl", organisationId, "attention"]`
- `["tl", organisationId, "portfolio"]`
- `["tl", organisationId, "changes", filters]`
- `["tl", organisationId, "meetings", { limit: 100 }]`

Filter objects are part of the change key, so Tenant A can never reuse
Tenant B intelligence. Organisation switching changes the namespace and
refetches tenant-scoped data.

Backend authorization remains authoritative. A 403 produces the standard
permission message; a 404 produces the standard not-found message. Neither
exposes another tenant’s data.

## 7. Performance / bounded fan-out decisions

Intentional decisions:

- Six parallel organisation reads on first load.
- One bounded meeting-list response instead of one request per change.
- Portfolio response reused as the entity directory.
- Bounded name joins only; no unbounded entity or meeting fan-out.
- Change-stream limit: 50.
- Repeated-signal limit: 25.
- Movement collection limit: 100.
- Attention display limit: 10.
- Recent-movement display limit: 10.
- Follow-up display limit: 8.
- Repeated, movement, and directory queries are independent of the filtered
  stream.
- A dedicated test asserts that no `/meetings/{id}` or `/entities/{id}`
  point lookup occurs while rendering the workspace.

## 8. Accessibility

The page preserves Stage 25+ standards:

- exactly one `h1`;
- logical `h2` section order;
- real links and buttons;
- accessible form labels;
- shareable controls usable by keyboard;
- text paired with every status tone;
- semantic lists;
- native disclosure controls for evidence;
- loading announcements through `role="status"`;
- errors through `role="alert"`;
- machine-readable timestamps through `<time dateTime>`;
- no interactive `div` elements;
- no color-only meaning.

## 9. Responsive behavior

Desktop uses an editorial main-plus-side workspace layout. Below 1024px the
page collapses to one readable column in source order.

Tables are not used for the intelligence stream because variable-length
evidence does not fit a rigid grid. Wide dependency paths wrap, long text
uses normal wrapping, and the existing horizontally scrollable table pattern
remains available elsewhere in the application.

Responsive validation was DOM/CSS-level because the repository has no
browser-testing infrastructure.

## 10. Tests added

### `intelligenceFormat.test.ts` — 8 tests

Covers control parsing, backend-order preservation, entity-type narrowing,
impact/dependency selection, newest-first movement, follow-up selection,
meeting-directory deduplication, freshness selection, and active-filter
detection.

### `intelligence.test.tsx` — 15 tests

Covers:

1. structural loading across all intelligence sections;
2. successful attention, stream, repeated, movement, and follow-up rendering;
3. calm empty states;
4. backend severity/change-type query parameters;
5. local entity-type narrowing without sending it to the backend;
6. rejection of unsupported filter values;
7. movement failure without losing attention, stream, or repeated signals;
8. filtered-stream failure without losing other sections;
9. directory failures with ID-based fallback links;
10. section retry and recovery;
11. forbidden responses without backend-error leakage;
12. organisation switching;
13. attention-to-entity navigation;
14. change-to-meeting navigation;
15. absence of per-signal entity/meeting lookups;
16. heading hierarchy and section order.

### `api/keys.test.ts`

Extended to cover organisation namespacing for Intelligence change filters
and the bounded meeting directory.

### `tests/test_frontend_contracts.py`

Added a live acceptance test that seeds an organisation, meeting lifecycle,
resolved dependency, blocked attention, repeated observation, filtered
changes, and meeting titles.

## 11. Full-stack acceptance results

The backend acceptance test proves that:

- an authenticated organisation can create meetings and entities;
- an exact mention resolves to the intended canonical entity;
- an explicit dependency statement creates a dependency record;
- blocked evidence produces `CRITICAL` attention with `ENTITY_BLOCKED`;
- portfolio records the blocked, critical entity and its action count;
- `STATE_BLOCKED`, `REPEATED_UNRESOLVED`, and `NEW_DEPENDENCY` filters return
  the seeded records;
- critical-severity filtering returns only critical records;
- meeting titles resolve from the bounded meeting list;
- missing entities return 404; and
- unknown organisations return 403.

Frontend integration tests then prove that the same contract shapes render,
navigate, filter, fail independently, switch tenants safely, and avoid
per-signal lookups.

## 12. Build/typecheck results

```text
npx tsc --noEmit
→ clean, no output

npm test
→ Test Files: 20 passed
→ Tests: 134 passed
→ Duration: 35.41s

npm run build
→ production build succeeded in 484ms
→ IntelligencePage chunk: 17.41 kB / 4.80 kB gzip

python -m pytest -q
→ 897 passed in 77.68s

python -m pytest tests/test_frontend_contracts.py tests/test_stage_24_security.py -q
→ 30 passed in 20.56s
```

No backend source files were modified.

## 13. Files changed

Created:

- `frontend/src/features/intelligence/useIntelligence.ts`
- `frontend/src/features/intelligence/IntelligenceSections.tsx`
- `frontend/src/features/intelligence/intelligence.css`
- `frontend/src/features/intelligence/intelligenceFormat.ts`
- `frontend/src/features/intelligence/intelligenceFormat.test.ts`
- `frontend/src/features/intelligence/intelligence.test.tsx`

Rewrote in place:

- `frontend/src/features/intelligence/IntelligencePage.tsx`

Modified for tested contract support:

- `frontend/src/test/harness.tsx`
- `frontend/src/api/keys.test.ts`
- `tests/test_frontend_contracts.py`

Created by this stage report:

- `STAGE_29_FINAL_REPORT.md`

## 14. Known limitations

- Exact organisation-wide action recommendations are unavailable because no
  organisation-level action read endpoint exists. The page links to entities
  that have backend-reported actions.
- No organisation-wide dependency-edge or impact-source list exists. Impact
  and dependency movement use backend-classified change records and portfolio
  association counts.
- No organisation-level trend endpoint exists. Repeated signals use the
  backend’s exact repeated-observation classification; no frontend trend
  engine was added.
- Entity-type filtering is client-side over the loaded records because the
  changes endpoint does not expose an entity-type query field.
- Meeting titles are available for meetings inside the bounded list of 100.
  Older meetings still navigate correctly but may fall back to shortened IDs.
- Movement and impact views can repeat records that also appear in the
  change stream because they are intentionally different curated views of
  overlapping backend data.

## 15. Visual QA limitations

No browser or headless-browser validation was performed because the
repository provides no browser-testing infrastructure.

Repository-level validation covered:

- heading hierarchy;
- DOM/source order;
- section composition;
- skeleton structure;
- empty and error states;
- responsive CSS;
- existing design tokens;
- spacing, borders, and elevation;
- navigation targets;
- accessible labels; and
- production code splitting.
