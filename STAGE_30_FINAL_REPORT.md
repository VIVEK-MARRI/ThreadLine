# Stage 30 Final Report: Product Integration and Release Hardening

## 1. Complete product audit

| Area | Current state | Issue | Proposed fix |
|---|---|---|---|
| Global navigation | All destinations existed; nested routes retained active state through `NavLink`. | No defect found. | None; added regression tests. |
| Mobile navigation | Drawer opened, navigated, and closed structurally. | Closed-drawer landmark had the same accessible name as the sidebar, creating duplicate landmark naming. | Renamed the drawer landmark to “Mobile navigation”; added open/navigate/close tests. |
| Dashboard → workspaces | Explore, attention, change, risk, and processing links existed. | No defect found. | Added end-to-end destination tests. |
| Meetings | List/detail, filters, processing, facts, mentions, dependencies, changes, and transcript existed. | A 403 detail response used a different error branch from an out-of-scope 404. | Mapped `forbidden` to the same tenant-safe unavailable state as `not-found`; added a regression test. |
| Entities | Directory/detail, timeline, risks, dependencies, impact, meetings, changes, memory, and actions existed. | No defect found. | Added navigation-return coverage. |
| Intelligence | Attention, change stream, repeated signals, movement, and follow-up paths existed. | No defect found. | Added cross-workspace destination tests. |
| Ask | The page called only the evidence-preview endpoint while describing generated answers. Evidence had meeting links but no entity links. Mutation state also had no tenant boundary. | Real product defect: misleading answer/evidence distinction, missing entity connectivity, and possible stale tenant answer. | Wired the real `/api/v1/query` answer endpoint, rendered generated answers separately from exact cited evidence, added entity and meeting links, and remounted the workspace on organisation change. |
| Actions | The route was a “coming next” shell whose description contradicted its empty state. | Real product defect: a primary destination was a dead end. | Rebuilt it as a read-only follow-up-path directory from real portfolio `action_count` values, linking each entity to its exact recommendations. No action-management behavior was added. |
| Settings | Profile, organisation, members, diagnostics, logout, and role-aware controls existed. | Real release blocker: opening Settings with no selected organisation evaluated tenant-scoped query keys and crashed before rendering. | Moved member and diagnostic queries into organisation-scoped child panels rendered only when an organisation is selected. |
| Route guards | Auth, organisation, splash, recovery, and role messaging existed. | Guard recovery used a plain anchor, causing a full document reload. | Changed the recovery action to a router `Link`; added a navigation test. |
| Organisation switching | Tenant cache cleared and scoped requests continued. | List-filter query state from one organisation persisted into another organisation. | Header organisation switches now preserve the route path but clear its search state; initial shareable-entry selection behavior is unchanged. |
| Query cache | Tenant namespaces and logout/session clearing were sound. | No new defect found. | Added stale-tenant, URL-reset, header-scope, and logout regression tests. |
| Auth/session | Login, bootstrap, restore, 401 handling, role-aware UI, and backend enforcement existed. | Logout and mid-session expiry lacked route-level regression coverage. | Added both. |
| URL state | Meeting, entity, and intelligence filters were shareable and invalid values normalized. | No defect found. | Added regression coverage for tenant-safe reset and destination navigation. |
| Loading/error/empty states | Structural skeletons and independent section states existed. | No systemic defect found. | Added lifecycle, partial-failure, retry, and fallback-identifier coverage. |
| Document titles | Every major route set a title. | No defect found. | Added route and dynamic detail-title coverage. |
| Design system | Existing tokens, badges, cards, tables, alerts, dialogs, and focus styles were used. | No redesign-worthy defect found. | No visual redesign. |
| Responsive behavior | Sidebar/drawer, grids, tables, forms, timelines, and dependency paths used established responsive patterns. | No code defect proven. | Validation remained DOM/CSS-level because no browser tooling exists. |
| Accessibility | Headings, labels, links/buttons, tables/lists, disclosures, alerts, and status text were consistent. | Duplicate navigation landmark naming. | Fixed as above; no redundant ARIA was added. |
| Performance | Bounded joins and tenant-scoped caching were already used. | No new N+1 or unbounded fan-out was introduced. | Added a regression test proving the new workspaces do not issue per-signal point lookups. |
| Global search | No global keyword-search backend endpoint exists. | Do not build a fake one. | Left unchanged; documented below. |

## 2. Issues discovered

1. Ask advertised answers but only fetched evidence previews.
2. Ask evidence omitted supported entity links.
3. Ask mutation state could theoretically persist across organisations.
4. Actions was a contradictory placeholder and a primary-navigation dead end.
5. Settings crashed for an authenticated user with no selected organisation.
6. Meeting-detail 403 handling differed from the tenant-safe entity-detail behavior.
7. Guard recovery used a full-page anchor instead of client-side routing.
8. Organisation switching preserved one tenant’s URL filters in another tenant.
9. Closed mobile drawer duplicated the sidebar’s accessible landmark name.

## 3. Issues fixed

1. Ask now posts to the real backend answer endpoint.
2. Generated answers and exact cited evidence are rendered in separate sections.
3. Evidence warnings, intent, timestamps, source references, source text, and
   cited identifiers are preserved.
4. Evidence now links to both supported entities and supported meetings.
5. Ask workspace remounts when the organisation changes, eliminating stale
   tenant answers.
6. Actions now lists portfolio entities with backend-reported action counts
   and links to exact entity recommendations.
7. Settings member/diagnostic queries are constructed only inside
   organisation-scoped panels.
8. Meeting 403 responses show the same safe unavailable state as missing or
   out-of-scope meetings.
9. Guard recovery navigates client-side.
10. Header organisation switching clears route search state while preserving
    the destination path.
11. The mobile drawer has a distinct accessible landmark name.

## 4. Navigation integration

Verified navigation now includes:

- Dashboard → Intelligence
- Dashboard → Entity
- Dashboard → Meeting
- Intelligence → Entity
- Intelligence → Meeting
- Entity → Meeting
- Entity → related Entity
- Meeting → Entity
- Meeting → Meetings list through breadcrumbs
- Entity → Entities list through breadcrumbs
- Ask → Entity
- Ask → Meeting
- Actions → Entity
- Guard recovery → Settings
- Mobile drawer → Intelligence
- Nested Meeting and Entity routes retain the correct active navigation state

All links use existing routes. No routing abstraction was added.

## 5. Cross-workspace connectivity

The connected investigation journey is now covered by real route transitions:

```text
Dashboard change/attention
→ Entity
→ Meeting
→ evidence

Dashboard
→ Intelligence
→ Entity
→ related Entity
→ related Meeting

Meetings
→ Entity
→ Intelligence-connected evidence

Entities
→ timeline
→ source Meeting
→ related Entity

Intelligence
→ Change
→ Entity
→ Meeting

Ask
→ generated answer
→ cited evidence
→ Entity/Meeting

Actions
→ portfolio-reported follow-up
→ Entity
→ exact recommended action
```

Entity and meeting destinations show real backend names. Evidence and source
links preserve backend identifiers and routes.

## 6. Auth/session lifecycle

Verified behavior:

- Unauthenticated users reach login or setup according to backend state.
- Valid login lands on the requested dashboard route.
- Guard preserves shareable `from` paths, including search parameters.
- Logout calls the backend, clears the token and query cache, and lands on login.
- A mid-session 401 clears local session state and redirects to login.
- Expired initial sessions return to login.
- Forbidden detail responses do not expose another tenant’s object.
- Missing tenant objects show the same safe unavailable experience.
- Backend authorization remains authoritative; frontend permission helpers are
  display hints only.

## 7. Tenant/cache audit

Findings and coverage:

- Every server-state query key retains the `["tl", organisationId, ...]`
  convention.
- Organisation switching clears cached server state before the new scope is
  applied.
- Logout clears cached server state and the stored token.
- Ask has no persistent answer cache; its workspace remounts by organisation.
- Switching organisations clears list-filter query state while retaining the
  route path.
- Tenant-switch tests assert that old organisation content disappears and
  only new organisation content renders.
- Query-key tests assert that Tenant A and Tenant B namespaces never match.
- Fetch-call tests assert that organisation-scoped headers change with the
  active tenant.
- Backend security and contract suites continue to enforce tenant isolation
  server-side.

## 8. Ask workspace audit

Ask now uses `POST /api/v1/query` and preserves:

- submitted question;
- generated answer;
- intent;
- warnings;
- generated timestamp;
- cited evidence only;
- evidence type, summary, source text, timestamp, source reference, entity ID,
  meeting ID, and mention ID where provided;
- insufficient-evidence answers without presenting them as facts.

The old evidence-only flow was removed because it contradicted the page’s
answer-oriented description. No confidence score, probability, ownership,
summary, or source is invented.

There is still no global keyword-search endpoint. Ask is a natural-language
answer interface; meetings, entities, and intelligence retain their existing
bounded searches.

## 9. Actions workspace audit

Backend capabilities found:

- Per-entity recommended actions: `GET /api/v1/entities/{entity_id}/actions`.
- Organisation-wide recommended-action counts:
  `portfolio.entities[].action_count`.
- No organisation-wide action-list, priority, owner, status, or management
  endpoint.

The Actions workspace therefore:

- lists portfolio entities with `action_count > 0`;
- preserves exact risk, state, impact, and action counts;
- links each row to the entity containing its exact recommendations;
- explicitly says exact recommendations live on entity pages;
- does not manage, prioritise, restate, or invent actions.

## 10. URL-state audit

Verified behavior:

- Meetings filters remain URL-driven and shareable.
- Entity filters remain URL-driven; unsupported values normalize safely.
- Intelligence severity, change type, and entity type remain shareable.
- Invalid Intelligence filter values are normalized and never sent.
- Organisation switching clears the active route’s query state.
- Initial organisation selection still preserves an incoming shareable URL.
- Back/forward behavior relies on standard React Router history; no custom
  history handling was added.

## 11. Loading/error/empty-state audit

Verified behavior:

- Major routes render structural skeletons rather than blank screens.
- Independent sections load and fail independently.
- Backend exception text is not exposed.
- Retry controls refetch the failed query.
- Empty states explain the absent domain object rather than saying “No data.”
- Follow-up and directory fallbacks use shortened identifiers only when a
  name/title directory is unavailable.
- Forbidden responses use safe permission messaging.
- Missing tenant objects use the same safe unavailable experience.

## 12. Accessibility audit

Verified behavior:

- One `h1` per route and logical heading order.
- Accessible labels for search, filters, forms, dialogs, navigation, drawers,
  menus, tables, and disclosures.
- Real links and buttons throughout the fixed flows.
- Native disclosure controls for evidence.
- `aria-expanded` behavior for mobile and account navigation.
- Distinct sidebar and mobile-navigation landmarks.
- Status, alert, progress, and timestamp semantics preserved.
- Status meaning remains paired with text.
- No interactive `div` controls were added.

## 13. Responsive audit

Validated through source, DOM, and CSS review:

- Desktop workspace grids remain intact.
- Mobile drawer structure opens, navigates, and closes.
- Tables retain horizontal-scroll treatment.
- Forms stack below applicable breakpoints.
- Header identity and organisation controls collapse safely.
- Long names, titles, evidence, dependency paths, and timelines wrap rather
  than overflow their containers.
- No new gratuitous visual tokens were introduced.

## 14. Performance audit

Findings:

- No new N+1 request loops were introduced.
- Ask issues one answer request per submission.
- Actions issues one portfolio request.
- Intelligence continues to use bounded collection responses.
- Entity and meeting names are joined from already-loaded collections where
  available.
- Bounded entity-name joins remain capped where point lookups are genuinely
  necessary.
- No state-management library, global store, architectural rewrite, or
  duplicate intelligence computation was added.
- A regression test asserts that Intelligence rendering does not issue
  per-signal `/meetings/{id}` or `/entities/{id}` requests.

## 15. Tests added

New frontend tests: **37**

- `product-shell.test.tsx`: **15**
  - Nested active navigation
  - Mobile navigation semantics
  - Logout and session clearing
  - Mid-session expiry
  - Tenant-safe URL reset
  - Client-side guard recovery
  - Forbidden details
  - Seven document-title cases

- `product-journeys.test.tsx`: **10**
  - Dashboard → Intelligence/Entity/Meeting
  - Intelligence → Entity/Meeting
  - Entity → Meeting/related Entity
  - Meeting → Entity
  - Meeting/Entity breadcrumb returns

- `ask.test.tsx`: **6**
  - Generated answer and cited evidence
  - Insufficient-evidence handling
  - Failure and retry
  - Tenant answer reset
  - Evidence → Entity navigation
  - Evidence → Meeting navigation

- `actions.test.tsx`: **6**
  - Loading/empty/error/retry
  - Portfolio-reported follow-up rows
  - Tenant switching
  - Follow-up → Entity navigation

No backend tests were added because the audit found no frontend-blocking
backend contract defect.

## 16. Live full-stack acceptance

Backend behavior was exercised through the real FastAPI application and test
client:

```text
python -m pytest -q
→ 897 passed in 66.86s

python -m pytest tests/test_natural_language.py tests/test_natural_language_hardening.py tests/test_actions.py tests/test_attention.py tests/test_portfolio.py -q
→ 85 passed in 0.69s

python -m pytest tests/test_stage_24_security.py tests/test_frontend_contracts.py -q
→ 31 passed in 14.36s
```

These suites cover authentication, tenant isolation, meetings, entities,
attention, portfolio, actions, natural-language answer behavior, evidence
handling, and frontend contract shapes.

Frontend navigation was verified through route-level integration tests using
backend-contract-shaped responses. There is no browser/headless test runner
in the repository, so these are not live browser end-to-end runs.

## 17. Exact build/typecheck/test results

```text
npx tsc --noEmit
→ clean, no output

npm test
→ Test Files: 24 passed
→ Tests: 171 passed
→ Duration: 40.71s

npm run build
→ production build succeeded in 863ms
→ ActionsPage chunk: present
→ AskPage chunk: present

python -m pytest -q
→ 897 passed in 66.86s
```

The frontend suite grew from 134 to 171 tests: **37 new tests, all passing.**

## 18. Files changed

Modified:

- `frontend/src/auth/guards.tsx`
- `frontend/src/components/layout/AppShell.tsx`
- `frontend/src/features/actions/ActionsPage.tsx`
- `frontend/src/features/ask/AskPage.tsx`
- `frontend/src/features/meetings/MeetingDetailPage.tsx`
- `frontend/src/features/settings/SettingsPage.tsx`

Created:

- `frontend/src/features/actions/actions.css`
- `frontend/src/features/actions/actions.test.tsx`
- `frontend/src/features/ask/ask.test.tsx`
- `frontend/src/test/product-journeys.test.tsx`
- `frontend/src/test/product-shell.test.tsx`

Created by this report:

- `STAGE_30_FINAL_REPORT.md`

No backend files were changed.

## 19. Known limitations

- Ask depends on the configured backend answer provider. If that provider is
  unavailable, the page reports the backend failure rather than generating a
  local answer.
- Actions has no organisation-wide recommendation feed because the backend
  exposes recommendations per entity. Exact recommendations remain on entity
  pages.
- There is no global keyword-search endpoint. Discovery uses navigation,
  breadcrumbs, contextual links, bounded list searches, and Ask.
- Meeting titles outside bounded loaded collections may fall back to
  shortened identifiers while preserving correct routes.
- Entity names remain unavailable until the portfolio snapshot loads; links
  preserve correct routes in that state.
- Back/forward behavior was validated through shareable URL state and React
  Router semantics, not through live browser history testing.

## 20. Browser-visual-QA limitations

No browser, headless-browser, screenshot, Playwright, Cypress, Puppeteer, or
equivalent tooling was found in the repository. Consequently:

- No desktop/tablet/mobile browser validation was performed.
- No screenshots were captured.
- No live browser navigation-history behavior was tested.
- No visual rendering beyond DOM structure and CSS review was verified.

The strongest available repository-level validation was performed instead.

## 21. Explicit list of anything intentionally NOT changed

- Backend source code.
- Backend tests.
- Backend intelligence architecture.
- React Query or application state architecture.
- Route paths or routing abstractions.
- Primary navigation structure.
- Design tokens or visual language.
- Dashboard, Meetings, Entities, or Intelligence business logic.
- Organisation-wide action management.
- Global keyword search.
- Trend detection or causal inference.
- Natural-language answer generation or citation validation.
- Unused future-oriented helpers such as `RequireRole`.
- Stage 31 or any future-stage feature.
- Git history: nothing was committed or pushed.
