# Stage 25 Final Report: Frontend Foundation

## Result

Frontend foundation complete: design system, authenticated app shell,
real backend auth integration, route guards, and feature page shells —
connected to the real FastAPI backend. No mock data anywhere in production.

```text
Frontend: npx tsc --noEmit            → clean (EXIT 0)
          npm run build               → production build succeeds (code-split)
          npm test (vitest run)       → 9 files, 52/52 passed (x2 consecutive)
Backend:  889 passed, 0 failed        → unchanged (no backend files modified)
          tests/test_frontend_contracts.py → 1 passed (live round-trip contract)
```

The frontend tests include integration-style route tests that stub `fetch`
against the *real* backend contracts (`src/test/harness.tsx`), plus one live
backend round-trip test (`tests/test_frontend_contracts.py`): bootstrap →
login → me → entities against a real server.

## What was built

### Stack

Vite 8 + React 19 + TypeScript (strict) + react-router-dom 7 + TanStack
Query 5 + lucide-react. Vitest 5 + Testing Library for tests. Vite dev
server proxies `/api` and `/health` to the FastAPI backend (port 8000).

### Design system (`src/components/ui/`, `src/styles/`)

- **Tokens** (`tokens.css`): warm paper neutrals + navy primary + muted
  teal support. Single source of truth for colour, type scale, spacing
  (4px base), radius, borders, shadows, motion, focus ring. Light mode only.
- **Base** (`base.css`): reset, visible focus rings, `sr-only`, reduced motion.
- **Components**: Button/IconButton, Input/Field/Textarea/Select/Checkbox,
  Badge/StatusBadge, Avatar, Card/Section, generic `Table<T>`, Tabs,
  Pagination, Breadcrumbs, Tooltip, Dropdown, Dialog/ConfirmationDialog,
  Toast (provider + hook), Alert, and the States set (Spinner/Skeleton/
  LoadingState/EmptyState/ErrorState/ProgressIndicator).
- **Accessibility bar**: one `<h1>` per screen, icon-only controls carry
  accessible names, keyboard-operable menus/dialogs, focus trapping, skip
  link, colour-plus-text status signals, reduced-motion support.

### App shell (`src/components/layout/AppShell.tsx`)

Responsive: fixed sidebar (wide) → drawer + scrim (narrow). Header holds
the organisation switcher and account menu with sign-out.

### Auth & tenant scope (`src/auth/`)

- `AuthProvider`: session restore, login, logout, bootstrap, 401 handling.
  Registers token supply + organisation reader with the API client.
- `OrganisationProvider`: current membership, auto-select, persist selection,
  tenant-scoped switching (clears query cache on switch).
- Route guards (`RequireAuth`, `RequireOrganisation`, `RequireRole`) and a
  role-permission hint map. Guards redirect or explain — they never fetch;
  backend remains the authority for every request.

### API client (`src/api/`)

Single fetch call-site (`client.ts`) with typed `ApiError`, auth headers,
organisation hint header, and one-shot 401 clearing. Feature modules:
auth, organisations, meetings, entities, intelligence (+ cache `keys`).
All types hand-verified against the backend OpenAPI.

### Routing (`src/app/router.tsx`)

13 routes, feature pages lazy-loaded and code-split. Public routes
(login/setup), protected shell, org-gated feature routes, settings.

### Feature pages (`src/features/`)

Page shells with real headers/sections and no invented data: Login,
Setup, SelectOrganisation, Dashboard, Meetings, MeetingDetail, Entities,
EntityDetail, Intelligence, Ask, Actions (placeholder), Settings.

## Bugs found and fixed during the stage

1. **AuthContext token race (production-breaking)**: `login()` set the token
   then immediately called `me()`, but the request-time token ref only
   updated on re-render — `/me` went out tokenless → 401 → login always
   failed. Fixed by updating `tokenRef.current` synchronously (same fix to
   `bootstrap()` and `clearSession()`).
2. **LoginPage error masking**: backend's safe "invalid email or password"
   was hidden behind a generic "session expired". Login now surfaces the
   API's user-facing message for `ApiError`.
3. **Field/aria-describedby**: the id was placed on a wrapper div instead of
   the control; `Field` now uses `cloneElement` to wire it onto the child.
4. **Flaky route tests**: the 3 tests that mount lazy pages (guards "renders
   protected content", both org-switching heading asserts) intermittently
   failed under the 9-worker full suite — the lazy `DashboardPage` chunk
   took >1000ms to mount (kills at 1175/1154/1561ms), exceeding the default
   `waitFor` timeout. Fixed by raising those assertions to a 15s wait. Two
   consecutive full-suite runs: 52/52 both times.

## Files created

- `frontend/` — Vite project: `package.json`, `vite.config.ts`,
  `tsconfig.json`, `index.html`, `src/main.tsx`, `src/test/setup.ts`
- `frontend/src/styles/tokens.css`, `base.css`
- `frontend/src/components/ui/` — Button, Input, Badge, Avatar, Card,
  Table, Tabs, Pagination, Breadcrumbs, Dropdown, Dialog, Toast, Alert,
  status (`status.ts`), styling (`controls.css`, `display.css`,
  `overlay.css`, `feedback/states.css`)
- `frontend/src/components/layout/` — AppShell, PageHeader, Logo,
  `shell.css`
- `frontend/src/components/feedback/States.tsx`
- `frontend/src/api/` — client, auth, organisations, meetings, entities,
  intelligence, keys
- `frontend/src/types/` — api, auth, meetings, entities, intelligence,
  query, jobs
- `frontend/src/auth/` — AuthContext, OrganisationContext, guards,
  permissions, session
- `frontend/src/app/` — router, providers
- `frontend/src/features/` — auth (Login/Setup/SelectOrganisation), dashboard,
  meetings, entities, intelligence, ask, actions, settings + `pages.css` etc.
- `frontend/src/hooks/useDocumentTitle.ts`
- `frontend/src/test/harness.tsx`
- Tests: `client.test.ts`, `keys.test.ts`, `permissions.test.ts`,
  `status.test.ts`, `components.test.tsx`, `guards.test.tsx`,
  `organisation-switching.test.tsx`, `routing.test.tsx`,
  `LoginPage.test.tsx` — 52 tests across 9 files
- `tests/test_frontend_contracts.py` — live backend round-trip contract test
- `frontend/DESIGN_SYSTEM.md` — design system documentation
- `AGENTS.md` — repo working notes with verified commands
- `STAGE_25_FINAL_REPORT.md` (this file)

## Files modified (this stage)

- `frontend/vite.config.ts` — vitest jsdom config with `pool: "forks"`
  (`maxWorkers: 2` in effect from vitest; spawn count is per-file isolation)

## Notes / limitations

- The org switcher and account menu live in the header ready for tenant
  workflows; feature pages are shells awaiting data stages.
- `tests/test_frontend_contracts.py` is hermetic (creates and cleans up its
  own users/orgs against a real server).
- One pre-existing deprecation warning from starlette's TestClient in the
  pytest run is unrelated to this stage.

## Acceptance checklist

- [x] Vite + React 19 + TypeScript scaffolded and type-clean
- [x] Design tokens + base styles define the visual language
- [x] Design system component library with CSS, keyboard + a11y handling
- [x] App shell with responsive sidebar/drawer and org switcher
- [x] Real backend auth (login/bootstrap/session restore/logout) wired
- [x] Organisation context with auto-select, persist, tenant-safe switching
- [x] Route guards for auth/org/role; backend remains authority
- [x] Feature page shells for meetings, entities, intelligence, ask, actions
- [x] Production build succeeds with code-split lazy routes
- [x] 52 frontend tests pass; backend 889 suite unchanged and green
- [x] Live frontend↔backend contract test passes
- [x] AGENTS.md + DESIGN_SYSTEM.md written