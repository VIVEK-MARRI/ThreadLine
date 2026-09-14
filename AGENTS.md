# ThreadLine — agent working notes

## Backend (`app/`)
- FastAPI + SQLAlchemy. Run the full suite:
  `python -m pytest` (everything, incl. security contract tests)
- Fast special-case runs that gate a stage (short and deterministic):
  `python -m pytest tests/test_security_contracts.py tests/quick_contracts.py -q`
- Boot for manual QA:
  `python -m uvicorn app.main:app --port 8000`
- All API paths are versioned under `/api/v1`. Remote first-run is open
  (`/api/v1/auth/bootstrap`); after an owner exists the server is locked.

## Frontend (`frontend/`)
- Vite + React 19 + TypeScript. All commands run inside `frontend/`.
  - Dev server: `npm run dev` (proxies `/api` and `/health` to backend on port 8000)
  - Typecheck: `npm run typecheck` (alias `npx tsc --noEmit`)
  - Unit/route tests: `npm test` (vitest, jsdom)
  - Production build: `npm run build` (typecheck + `vite build`)
- Tests: `src/**/*.test.{ts,tsx}`. No production mock data; integration-style
  route tests use `src/test/harness.tsx` to stub fetch against real backend
  contracts.
- The API client in `src/api/client.ts` is the only place that calls fetch.

## Process conventions
- Verify a stage with the typecheck + `npm test` / `pytest` that gated it, then
  write a `STAGE_NN_FINAL_REPORT.md` at the repo root, commit, and push only
  when the user asks.
- Never stage or commit unrelated files.