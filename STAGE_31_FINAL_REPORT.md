# Stage 31 — Real Product Validation & Release Readiness

## 1. Environment and Tooling Audit

- **No CI/CD**: No `.github/` directory. No CI configuration anywhere in the repo.
- **No root `package.json`**: All frontend commands run inside `frontend/`.
- **No prior E2E framework**: No Playwright, Cypress, or Puppeteer in `node_modules`. A stale npx cache of `playwright-core@1.61.0` existed at `C:\Users\vivek\AppData\Local\npm-cache\_npx\...` but was not wired to the project.
- **Playwright browser cache**: `C:\Users\vivek\AppData\Local\ms-playwright\` contained Chromium 149.0.7827.55 (headless shell + ffmpeg) pre-installed from the npx cache.
- **Python backend venv**: `.venv\` at repo root with `uvicorn`, `sqlalchemy`, `httpx`, and all test dependencies already installed.

## 2. Framework Choice

Playwright + Chromium (browser-only, no server-side logic in tests).

**Why Playwright**:
- Single framework for E2E, accessibility (via `@axe-core/playwright`), responsive overflow checks, and console/network interception.
- Built-in trace, screenshots, and API request helpers (`loginViaApi`, `selectOrganisation`).
- First-class Windows support with `@playwright/test@1.61.0` + `@axe-core/playwright@4.13.0` (exact, pinned).

**Why Chromium-only (for now)**:
- Firefox and WebKit lack `headless shell` mode. Running a full headed browser in CI-less environments adds instability without value in a solo-dev Windows workflow.
- The real bugs found (auth 403, observer stranding, contrast, mobile overflow) are layout/JS bugs, not cross-engine rendering issues.

## 3. Setup

| File | Role |
|------|------|
| `frontend/playwright.config.ts` | Servers (uvicorn:8000 + vite preview:4173), globalSetup, Chromium, workers 2 |
| `frontend/e2e/global-setup.ts` | Seeds two orgs, 3 meetings, 3 entities, blocked dependency, memberless user |
| `frontend/e2e/helpers.ts` | `loginViaApi`, `selectOrganisation`, `resolveEntityId`, `trackBrowserIssues`, `expectNoSevereBrowserIssues`, `expectNoHorizontalOverflow`, `expectNoAxeViolations` |
| `frontend/e2e/auth.spec.ts` | 5 auth lifecycle tests |
| `frontend/e2e/journeys.spec.ts` | 6 end-to-end journeys (A–F) |
| `frontend/e2e/tenant.spec.ts` | 2 tenant isolation tests |
| `frontend/e2e/responsive.spec.ts` | 3 overflow checks + drawer test + screenshot test (5 total) |
| `frontend/e2e/accessibility.spec.ts` | 2 tests (axe scan + keyboard tab/escape) |
| `frontend/e2e/url-history.spec.ts` | 2 URL persistence tests |
| `frontend/e2e/states.spec.ts` | 3 state/error edge-case tests |
| `frontend/e2e/console-network.spec.ts` | 1 test (quiet routes, bounded API traffic) |
| `frontend/package.json` | `"test:e2e": "npm run build && playwright test"` |
| `frontend/vite.config.ts` | `preview.port: 4173` + backend proxy |
| `frontend/.gitignore` | Adds `e2e/screenshots`, `test-results`, `playwright-report` |

## 4. Journeys A–F

| Journey | Path | Assertions |
|---------|------|-----------|
| A | Dashboard → Intelligence → Entity → Meeting | Navigation flow, headings, entity links, change links |
| B | Meetings → Detail → Entity → Related Entity | Meeting list, detail, entity hop, related entity hops |
| C | Intelligence Change → Entity → Meeting | Change stream links, entity detail, meeting detail |
| D | Ask → Answer → Evidence → Entity AND Meeting | Submit question, cited evidence section, entity link, meeting link |
| E | Actions → Entity → Recommendations | Action portfolio links, entity detail, "Escalate the blocker" text |
| F | Settings → Org Switch → Changed Content → Logout | Org switch swaps entities, logout clears session |

All 6 journeys pass.

## 5. Screenshots

Captured at `frontend/e2e/screenshots/`:

- `dashboard-1440.png` — full desktop dashboard (1440×900)
- `entity-detail-1440.png` — entity detail workspace, full page (1440×900)
- `ask-mobile-390.png` — Ask ThreadLine mobile view (390×844)

## 6. Responsive Validation

| Viewport | Routes | Overflow | Notes |
|----------|--------|----------|-------|
| Desktop 1440×900 | 9 routes | 0px | — |
| Tablet 1024×768 | 9 routes | 0px | — |
| Mobile 390×844 | 9 routes | 0px | Logo wordmark hidden ≤600px to prevent header overflow |

The mobile drawer opens, navigates to "Meetings", auto-closes, and shows no overflow. Escape key closes the drawer and restores focus to the hamburger button.

## 7. Accessibility: Axe Scans

axe-core (with `@axe-core/playwright@4.13.0`) scans on **dashboard**, **intelligence**, and **ask**:

- **colour-contrast**: All nodes pass after `--tl-ink-muted` token fix (#8a8175 → #6b6357).
- **landmark-* / region / list / image-alt**: All pass.
- **target-size**: Passes (hamburger ≥24×24 at mobile).
- 6 violations initially found (dashboard `.tl-meta`, `.tl-dash-evidence`, `.tl-dash-freshness`) — all resolved by the contrast token change.

## 8. Accessibility: Keyboard Navigation

- **Tab order**: Skip link → "Open navigation" (mobile) / "Dashboard" link (desktop) → Header links → Account dropdown.
- **Escape**: Pressing Escape closes the mobile drawer and returns focus to the hamburger button (new `useRef`/`useEffect` in AppShell).
- **Drawer navigates with Enter/Space**: All nav links inside the drawer are focusable and activate via keyboard.

## 9. Console and Network Audit

`e2e/console-network.spec.ts` visits Dashboard, Intelligence, Entities, Ask, Meetings, Actions, Settings and checks:

- **console.error**: 0 unexpected errors (benign "Failed to load resource:" noise filtered; React Query cancels are counted separately).
- **Failed requests**: Only expected failures (route aborts in states tests). Normal routes: 0 failed requests.
- **API calls ≤ 12 per page load**: All pages stay within budget.

## 10. Tenant Isolation

`e2e/tenant.spec.ts` (2 tests):

1. **Organisation switch swaps content, resets filters, and clears answers**: Switching from Alpha to Beta shows Beta-only entities, clears URL filters and Ask answer.
2. **Direct cross-tenant route is safely unavailable**: Navigating to Alpha entity detail while on Beta shows "This entity isn't available" — no data leak.

## 11. Auth and Session

`e2e/auth.spec.ts` (5 tests):

1. **Unauthenticated visit to a protected route lands on login** — 302 redirect, "Welcome back" heading.
2. **Login preserves the return-to route** — redirect after login to original destination.
3. **Logout clears the session and protects routes again** — logout destroys session, re-protected.
4. **Invalid stored session returns to login** — corrupted token clears gracefully.
5. **Memberless account sees safe recovery, not a crash** — shows "No organisation yet" with "Go to settings" link.

## 12. URL and History

`e2e/url-history.spec.ts` (2 tests):

1. **Meeting and entity filters survive reload, back, and forward**: Status and search filters persist across page reload. Browser back/forward navigation preserves filter state.
2. **Intelligence filters persist, normalise, and reset on organisation switch**: Invalid filter values normalise on reload. Org switch clears filters (expected: route guard resets tenant-scoped state).

## 13. Issue Found: Backend Multi-Membership Auth Bug (403)

**Severity**: Critical — blocked login entirely for users with 0 or several organisations.

**Root cause**: `require_user()` in `app/api/auth.py` depended on `get_request_context`, which resolves an organisation scope before identity validation. A user with multiple memberships got 403 on `/auth/me` and `/orgs` — the frontend could not bootstrap and was stuck on login.

**Fix**: `require_user()` now validates the token only (identity check). Tenant-specific endpoints (`/meetings`, `/entities`, `/query`, etc.) use their own `require_org_context()` dependency — unchanged.

**Regression test**: `tests/test_stage_24_security.py::test_s24_multi_membership_identity_needs_no_org_scope` — 28/28 pass.

## 14. Issue Found: Frontend Reselect-Stranding Bug (Stuck Loading)

**Severity**: High — switch to current org left change stream/attention in perpetual loading.

**Root cause**: `OrganisationContext.select()` always called `queryClient.clear()` + identity setState, even when the selected organisation was already active. `clear()` destroyed all React Query observers; in-flight fetches (aborted by route change) resolved but no re-observer was attached — the UI was stuck on "Loading..." with no error path.

**Fix**: Two guards:
1. `OrganisationContext.select()` — early-return (no-op) when `organisationId === selectedIdRef.current`.
2. `AppShell.handleSelectOrganisation()` — return early when same org is already selected.

**Regression test**: `src/test/product-shell.test.tsx::"re-selecting the active organisation mid-flight keeps the pending query alive"` — pending query rejection surfaces error UI. 172/172 pass.

## 15. Issue Found: Axe Colour-Contrast Failure

**Severity**: Medium — inaccessible text contrast on dashboard secondary text.

**Nodes affected**: `.tl-meta`, `.tl-dash-row-change .tl-dash-evidence`, `.tl-dash-freshness`.

**Root cause**: `--tl-ink-muted: #8a8175` (contrast ratio ≈ 3.2:1 against white, fails WCAG AA ≥ 4.5:1).

**Fix**: `--tl-ink-muted: #6b6357` (contrast ratio ≈ 5.5:1). All six axe nodes pass after rebuild.

## 16. Issue Found: Mobile Header Overflow

**Severity**: Medium — 23px horizontal overflow at 390px viewport width.

**Root cause**: `.tl-header-brand` contains a `.tl-logo-word` text element that, combined with the account dropdown, exceeds 390px at mobile widths.

**Fix**: `shell.css` — hide `.tl-header-brand .tl-logo-word` at `≤600px` via `display: none`. The logo icon (`svg`) remains visible.

## 17. Issue Found: Journey D Ground-Truth Mismatch

**Severity**: Low — test asserted evidence that cannot exist for the chosen question.

**Root cause**: Journey D asked "What is blocking e2e payment gateway?" → intent classifier returns `ENTITY_DEPENDENCIES`, evidence retrieval returns only relationship + entity items (no `meeting_id`). The "Open source meeting" link (which requires `item.meeting_id`) could never render.

**Fix**: Changed question to "What happened to e2e payment gateway?" → `ENTITY_HISTORY` intent → retrieval includes OBSERVATION evidence with `meeting_id: e2e-alpha-launch`, which renders the meeting link. Journey D now follows both entity AND meeting hops.

## 18. Tests Added

**New E2E tests** (26):

| Spec file | Tests | Notes |
|-----------|-------|-------|
| `auth.spec.ts` | 5 | Login, logout, session expiry, return-to, memberless recovery |
| `journeys.spec.ts` | 6 | End-to-end flows A–F |
| `tenant.spec.ts` | 2 | Org switch, cross-tenant route |
| `responsive.spec.ts` | 5 | 3 overflow checks + drawer + screenshots |
| `accessibility.spec.ts` | 2 | Axe scan + keyboard Escape |
| `url-history.spec.ts` | 2 | Filter persistence, org switch reset |
| `states.spec.ts` | 3 | Delayed skeletons, abort+retry, empty/forbidden/not-found |
| `console-network.spec.ts` | 1 | Console quietness + API call budget |

**New vitest tests** (1):
- `product-shell.test.tsx` — "re-selecting the active organisation mid-flight keeps the pending query alive" (regression for #14).

**New pytest tests** (1):
- `test_stage_24_security.py::test_s24_multi_membership_identity_needs_no_org_scope` (regression for #13).

## 19. Exact Counts

| Gate | Count |
|------|-------|
| `npx tsc --noEmit` | Clean (0 errors) |
| `npm test` (vitest) | 24 files / **172 tests** / all pass |
| `npm run build` | ok (510ms) |
| `python -m pytest -q` | **898 tests** / all pass |
| `npm run test:e2e` (Playwright) | **26 tests** / all pass |

## 20. CI Changes

None. No `.github/` directory exists; no CI configuration modified.

## 21. Limitations and Browsers

| Item | Value |
|------|-------|
| Browser | Chromium 149.0.7827.55 only |
| Viewports | 1440×900 (desktop), 1024×768 (tablet), 390×844 (mobile) |
| Firefox | Not tested |
| WebKit | Not tested |
| E2E retries | 0 (fail immediately) |
| Workers | 2 (parallel test execution) |
| Timeout | 120s per test, 15s per assertion |
| Screenshots | Captured on passing runs at `e2e/screenshots/` |

## 22. Release-Readiness Conclusion

**Product passes all validation gates.**

Three genuine release blockers were found and fixed:
1. **Backend auth 403** — multi-membership users could not log in at all.
2. **Frontend reselect-stranding** — switching to the current org left pages stuck on loading skeletons.
3. **CSS contrast** — secondary text failed WCAG AA.

Two additional defects fixed:
4. **Mobile header overflow** — 23px horizontal scroll at 390px.
5. **Journey D ground-truth** — test corrected to match actual backend evidence behavior.

All 26 E2E tests pass (auth, 6 journeys, tenant, accessibility, responsive, console/network, URL history, state edge cases). The real build produces no tsc errors, 172 vitest tests pass, and 898 pytest tests pass. Three representative screenshots captured.

The product is ready for release on explicit commit request.
