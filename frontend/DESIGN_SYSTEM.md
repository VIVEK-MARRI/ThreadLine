# ThreadLine Design System

Design system for the ThreadLine web app. One source of truth for visual
language, components, and the conventions every screen must follow.

**Identity:** warm paper neutrals, a deep navy primary, and a muted
blue-teal support colour. Light mode only. No gradients, no glassmorphism,
no decorative colour — every token has a job.

---

## 1. Tokens (`src/styles/tokens.css`)

All values live as CSS custom properties on `:root`. **Components never
hard-code colors, spacing, or radii** — they reference tokens.

### Colour

| Group | Tokens |
| --- | --- |
| Neutral (warm paper) | `--tl-paper`, `--tl-surface`, `--tl-surface-sunken`, `--tl-ink`, `--tl-ink-secondary`, `--tl-ink-muted`, `--tl-ink-faint`, `--tl-line`, `--tl-line-strong` |
| Primary (navy) | `--tl-primary-950/900/800/700/600/100/50`, `--tl-on-primary` |
| Supporting (teal) | `--tl-teal-*` |
| Semantic | `--tl-success-*`, `--tl-warning-*`, `--tl-danger-*`, `--tl-info-*` (each has `-ink`, `-bg`, `-line`) |
| Attention | `--tl-attention-bg`, `--tl-attention-line` |

### Typography

`--tl-font-sans`, `--tl-font-mono`, then a fixed scale:

| Token | Use | Size |
| --- | --- | --- |
| `--tl-text-display-*` | Page titles | 30px |
| `--tl-text-title-*` | Screen/page headers | 22px |
| `--tl-text-section-*` | `h2` section titles | 16px |
| `--tl-text-card-*` | Card headings | 14px |
| `--tl-text-body-*` | Body copy | 14px |
| `--tl-text-secondary-*` | Secondary text | 13px |
| `--tl-text-caption-*` | Captions | 12px |
| `--tl-text-meta-*` | Labels/meta, uppercase tracked | 11px |

### Spacing, radius, borders, shadows, layout, motion

- Spacing: `--tl-space-xs/sm/md/lg/xl/2xl/3xl` (4px base). Page padding and
  section gaps derive from `--tl-page-padding` / `--tl-section-gap`, which
  tighten on small screens.
- Radius: `--tl-radius-sm` (6, controls), `--tl-radius-md` (8, cards),
  `--tl-radius-lg` (12, dialogs), `--tl-radius-full` (pills).
- Borders: `--tl-border`, `--tl-border-strong`. Borders come before shadows.
- Shadows: `--tl-shadow-sm/md/lg` — only where elevation is real.
- Layout: `--tl-header-height`, `--tl-sidebar-width`, `--tl-content-max`.
- Motion: `--tl-duration-fast/base`, `--tl-ease` — fast, subtle, purposeful.
- Focus: `--tl-focus-ring` — every interactive element uses it.

---

## 2. Base styles (`src/styles/base.css`)

- Modern reset (`box-sizing`, margin zeroing, media element defaults).
- Visible `:focus-visible` rings; focus styles suppressed for mouse only.
- `.tl-sr-only` for screen-reader text.
- `prefers-reduced-motion` disables non-essential transitions.
- Page background/ink/type are set from tokens.

---

## 3. Components (`src/components/ui/`)

Composable, presentational, and styling only through tokens + `tl-` classes.
Each component maps to one of the stylesheets in the table below.

| Component | File | CSS |
| --- | --- | --- |
| `Button` / `IconButton` | `Button.tsx` | `controls.css` |
| `Field`, `Input`, `Textarea`, `Select`, `Checkbox` | `Input.tsx` | `controls.css` |
| `Badge`, `StatusBadge` / status tones | `Badge.tsx`, `status.ts` | `display.css` |
| `Avatar` | `Avatar.tsx` | `display.css` |
| `Card`, `Section` | `Card.tsx` | `display.css` |
| `Table<T>` | `Table.tsx` | `display.css` |
| `Tabs`, `Pagination`, `Breadcrumbs` | `Tabs.tsx` et al. | `display.css` |
| `Tooltip`, `Dropdown` | `Dropdown.tsx` | `overlay.css` |
| `Dialog`, `ConfirmationDialog` | `Dialog.tsx` | `overlay.css` |
| `Toast`, `ToastProvider` | `Toast.tsx` | `overlay.css` |
| `Alert` | `Alert.tsx` | `feedback/states.css` |
| `Spinner`, `Skeleton`, `LoadingState`, `EmptyState`, `ErrorState`, `ProgressIndicator` | `feedback/States.tsx` | `feedback/states.css` |
| `PageHeader`, `AppShell`, `Logo` | `components/layout/` | `layout/shell.css` |

### Anatomy / conventions

- **`Button`** — variants `primary | secondary | ghost | danger`, sizes
  `sm | md`. `loading` disables and swaps content for an inline spinner.
  `IconButton` requires a `label` prop that becomes the accessible name.
- **`Field`** — wraps label + control + hint/error and wires `htmlFor`/`id`s.
  `aria-describedby` is cloned onto the child control so hints and errors
  are announced with the field. Pass `invalid` to `Input`/`Textarea`/`Select`
  for error styling + `aria-invalid`.
- **`Table<T>`** — generic over row type; `columns`, `rows`, `keyOf`,
  optional `caption` (sr-only) and `empty` fallback. Align and width are
  per-column. Wrap in `.tl-table-scroll` on narrow screens.
- **`Dropdown`** — accessible menu (`role=menu`, Escape + outside-click).
  Items are `{ id, label, description?, danger?, disabled? }`.
- **`Dialog`** — focus-trapped modal with labelled heading, Escape dismissal,
  and body scroll lock. `ConfirmationDialog` is the standard confirm flow.
- **`Toast`** — transient feedback via `ToastProvider` + `useToast`. Use for
  mutation results, never for primary navigation or errors that need action.
- **`States`** — the three product states every feature screen must handle:
  - `LoadingState`/`Skeleton` during fetch.
  - `EmptyState` when a collection is empty — explains + offers a next action.
  - `ErrorState` — uses `userFacingMessage()` so backend exception text never
    leaks; optional `onRetry`.

---

## 4. Product states

Every feature screen follows the same rhythm:

1. **Loading** — `Skeleton` or `LoadingState` matching the final layout.
2. **Empty** — `EmptyState` with a concrete next step.
3. **Error** — `ErrorState` with a retry where the action is retryable.
4. **Ready** — data layout with table/cards/list.

Toast appears only for completed mutations; destructive actions confirm via
`ConfirmationDialog` before executing.

---

## 5. Layout & routing

- **`AppShell`** — responsive: fixed sidebar on wide screens, drawer + scrim
  on narrow. Header carries the organisation switcher and the account menu.
- **`PageHeader`** — breadcrumbs + `<h1>` + description + right-aligned
  actions. Every feature route starts with it (single `<h1>` per screen).
- **Pages** — `div.tl-page` → `PageHeader` → sections. Route sections live in
  `src/features/<feature>/`, lazy-loaded in `src/app/router.tsx`.

---

## 6. Data & API conventions

- **API client** (`src/api/client.ts`) is the only place that calls `fetch`.
  All feature modules (`src/api/*.ts`) go through it. Errors surface as typed
  `ApiError`; helpers translate them to user-safe copy.
- **Query keys** (`src/api/keys.ts`) segment by organisation — no two tenants
  can merge caches. Switching organisations clears the query client.
- **Auth** (`src/auth/`) — `AuthProvider` owns session + 401 handling;
  `OrganisationProvider` owns the active membership; route guards
  (`RequireAuth`, `RequireOrganisation`, `RequireRole`) redirect or explain,
  they never fetch. The backend remains the authority for every request.

---

## 7. Accessibility bar

- One `<h1>` per screen (from `PageHeader`).
- Every icon-only control has an accessible name (`aria-label`/`label` prop).
- Keyboard: all interactive controls reachable and operable; dialogs trap
  focus; dropdowns dismiss on Escape; skip link to `#tl-main` in the shell.
- Colour is never the only signal — `StatusBadge` pairs tone with text.
- Motion respects `prefers-reduced-motion`.