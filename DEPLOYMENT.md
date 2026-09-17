# ThreadLine Deployment Guide

ThreadLine is a single-node modular monolith. The backend (FastAPI + SQLite) and frontend (React SPA) are deployed together on one machine.

## Prerequisites

- Python 3.12+
- Node.js 22+
- ~500 MB disk (application + database)

## Architecture

```
┌─────────────────────────────────────────┐
│  Reverse proxy (nginx, Caddy, or similar) │
│  :443 (HTTPS)                           │
├──────────────┬──────────────────────────┤
│ Static files │ /api/* → backend         │
│ (frontend)   │ /health → backend        │
└──────────────┴──────────────────────────┘
                      │
              ┌───────▼────────┐
              │ FastAPI (uvicorn)│
              │ :8000 (internal)│
              └───────┬────────┘
                      │
              ┌───────▼────────┐
              │ SQLite (.threadline/)│
              └────────────────┘
```

## Environment Variables

Copy `.env.example` to `.env` and set:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SOURCE_REPOSITORY_BACKEND` | **Yes** | `in_memory` | Set to `database` for production |
| `SOURCE_DATABASE_PATH` | No | `.threadline/threadline.db` | SQLite database file |
| `BACKGROUND_WORKER_ENABLED` | **Yes** | `false` | Set to `true` for production |
| `OPENAI_API_KEY` | No | — | Only if using real LLM extraction |
| `EXTRACTION_PROVIDER` | No | `openai` | `openai` or `fake` |
| `NL_PROVIDER` | No | `fake` | `fake` or `openai` (for Ask answers) |
| `AUTH_OPEN_BOOTSTRAP` | No | `true` | Set to `false` after first owner is created |
| `PROACTIVE_INTELLIGENCE_ENABLED` | No | `false` | Recurring org scans (Stage 34); enable explicitly |
| `PROACTIVE_INTELLIGENCE_INTERVAL_SECONDS` | No | `3600.0` | Seconds between scan ticks; must be > 0 |
| `QUERY_RATE_LIMIT_REQUESTS` | No | `60` | Per-user query quota per window (Stage 34) |
| `QUERY_RATE_LIMIT_WINDOW_SECONDS` | No | `60.0` | Rate-limit window; must be > 0 |

No JWT signing secret is required — sessions are opaque server-side tokens (only hashes stored).

## 1. Install Dependencies

```bash
# Backend
python -m venv .venv
.venv/bin/activate          # Linux/macOS
pip install -r requirements.txt

# Frontend
cd frontend
npm ci
npm run build              # produces frontend/dist/
cd ..
```

## 2. Configure Production

```bash
cp .env.example .env
# Edit .env:
#   SOURCE_REPOSITORY_BACKEND=database
#   BACKGROUND_WORKER_ENABLED=true
#   AUTH_OPEN_BOOTSTRAP=true  (temporary — for first boot only)
```

## 3. Start the Backend

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The backend:
- Creates `.threadline/threadline.db` on first start
- Runs idempotent schema migrations (versions 1–6)
- Starts the background worker when `BACKGROUND_WORKER_ENABLED=true`
- Exposes `/health` for liveness checks

## 4. Serve the Frontend

Serve `frontend/dist/` with your reverse proxy. Configure it to:

- Serve all static files from `frontend/dist/`
- Proxy `/api/*` and `/health` to `http://127.0.0.1:8000`

### nginx example

```nginx
server {
    listen 443 ssl;
    server_name threadline.example.com;

    # SSL — use your own certs or Let's Encrypt
    ssl_certificate     /etc/ssl/threadline.crt;
    ssl_certificate_key /etc/ssl/threadline.key;

    # Frontend static files
    root /path/to/threadline/frontend/dist;
    index index.html;

    # SPA fallback
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Backend API
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000;
    }
}
```

## 5. Bootstrap the First Owner

On first boot, while `AUTH_OPEN_BOOTSTRAP=true` and no users exist:

```bash
curl -X POST http://localhost:8000/api/v1/auth/bootstrap \
  -H "Content-Type: application/json" \
  -d '{"organisation_name": "Acme", "slug": "acme",
       "admin_email": "admin@acme.com", "password": "choose-a-strong-password"}'
```

Then set `AUTH_OPEN_BOOTSTRAP=false` in `.env` and restart.

**Bootstrap lifecycle guarantee (enforced at runtime, not by convention):**
bootstrap is open only while the flag is on *and* zero users exist. The
moment the first owner is created, `/api/v1/auth/bootstrap` returns 403
and anonymous data access returns 401 — even if the flag is still `true`
(e.g. the process is never restarted). An unreadable identity store fails
closed. There are no default credentials. Setting the flag to `false`
after bootstrapping is still required: it removes the open-bootstrap code
path entirely instead of relying on the user-count check.

## 6. Health Checks

```
GET /health          → {"status": "healthy"}
GET /health/diagnostics → backend storage + job health (admin only)
```

## 7. Persistent Storage

| Path | What | Backup? |
|------|------|---------|
| `.threadline/threadline.db` | All source data, users, sessions, jobs | **Yes** — this is the entire application state |
| `.threadline/semantic_index.json` | Derived semantic vectors | No — rebuildable from source data |

SQLite WAL mode is enabled. Use SQLite's `.backup` command or filesystem-level backup for consistent snapshots.

## 8. Backup

```bash
# Online backup (safe while the server runs)
sqlite3 .threadline/threadline.db ".backup '/backup/threadline-$(date +%Y%m%d).db'"
```

Back up `.threadline/threadline.db` regularly. The semantic index can be rebuilt by re-processing meetings.

## 9. Rollback

1. Stop the backend
2. Restore `.threadline/threadline.db` from backup
3. Restart the backend

Schema migrations are forward-only. To rollback a migration, restore from a pre-migration database backup.

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| 401 on all endpoints | Bootstrap closed, no users | Set `AUTH_OPEN_BOOTSTRAP=true`, restart, create owner |
| Meetings stuck in PENDING | Worker not enabled | Set `BACKGROUND_WORKER_ENABLED=true`, restart |
| `database is locked` | Multiple processes | Ensure only one uvicorn process writes to the DB |
| Frontend shows blank | Missing production build | Run `cd frontend && npm run build` |
| No NL answers | NL provider is `fake` | Set `NL_PROVIDER=openai` and `OPENAI_API_KEY` |
| 429 on Ask/query | Query quota consumed | Wait for the window (`Retry-After`) or raise `QUERY_RATE_LIMIT_*` |
| No proactive scans | Scheduler disabled (default) | Set `PROACTIVE_INTELLIGENCE_ENABLED=true`, restart |

## 11. Proactive Intelligence (Stage 34)

Disabled by default. When `PROACTIVE_INTELLIGENCE_ENABLED=true` (and the
background worker is enabled), the process-local worker tick enqueues one
`ORGANISATION_INTELLIGENCE_SCAN` job per active organisation every
`PROACTIVE_INTELLIGENCE_INTERVAL_SECONDS` (must be > 0; 1800–7200
recommended). Each scan reuses the existing deterministic intelligence
services, records a SUCCEEDED job row carrying the observed source
watermark plus deterministic signal IDs, and reports only genuinely new
signals — a scan never alerts merely because it ran. Latest scan state is
visible at `GET /api/v1/intelligence/scan-status` and on the Intelligence
page ("Proactive scan" panel). This is single-node scheduling: no
distributed coordination is claimed.

## 12. Query Rate Limiting and Fallback (Stage 34)

`POST /api/v1/query` and `POST /api/v1/query/evidence` share one
per-authenticated-user sliding window (`QUERY_RATE_LIMIT_REQUESTS` per
`QUERY_RATE_LIMIT_WINDOW_SECONDS`; anonymous bootstrap callers fall back
to client IP). Over-quota requests receive HTTP 429 with a machine-readable
`{"error": "rate_limited", ...}` body and a `Retry-After` header. Login's
per-email throttle is a separate, unchanged mechanism. Limitation: each
process enforces its own window — this is overload protection for the
single-node deployment, not a distributed global guarantee.

Unmatched questions no longer always reject immediately: an UNKNOWN intent
consults a conservative fallback that probes bounded, revision-guarded,
organisation-scoped semantic evidence. Only overlapping, citable evidence
(≥2 shared whole-word tokens, attributed to an entity or meeting) reaches
the standard evidence→answer pipeline; anything else keeps the historical
safe rejection. No guessed answers are ever generated.

## 13. Live OpenAI Integration Tests (Stage 34)

The default suite is network-free. Real provider calls run only with
`RUN_LIVE_OPENAI_TESTS=true` **and** a configured `OPENAI_API_KEY`, and
assert structure only (never model output text). No live-provider CI job
exists (no repository secrets are configured); do not claim live coverage
from ordinary CI.
