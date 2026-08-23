# Threadline

> **Threadline** transforms meeting data into structured organisational memory, insights, and proactive risk intelligence.

---

## What is Threadline?

Threadline is an evidence-backed AI platform that ingests meeting transcripts and builds a persistent, queryable record of your organisation's decisions, commitments, risks, and context.

The long-term intelligence pipeline is:

```
Meeting Ingestion → Information Extraction → Entity Resolution
    → Cross-Meeting Correlation → Temporal State Engine
    → Organisational Memory → Retrieval & AI Reasoning
    → Proactive Intelligence
```

**Today's implementation** covers the first stage only: clean meeting ingestion and retrieval over a REST API.

---

## Getting Started

### 1. Create a virtual environment

```bash
# Windows
py -m venv .venv

# macOS / Linux
python3 -m venv .venv
```

Activate it:

- **Windows (PowerShell):** `.venv\Scripts\Activate.ps1`
- **macOS / Linux:** `source .venv/bin/activate`

---

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

---

### 3. Run the application

```bash
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`.

---

### 4. Explore the API documentation

Open your browser and navigate to:

- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

---

## API Endpoints

### `GET /health`

Returns the operational status of the service.

```bash
curl http://localhost:8000/health
```

```json
{ "status": "healthy" }
```

---

### `POST /api/v1/meetings` — Ingest a meeting

```bash
curl -X POST http://localhost:8000/api/v1/meetings \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Payment Integration Weekly Sync",
    "transcript": "Rahul reported that the payment provider API is still unstable. Priya asked him to investigate the issue before Friday.",
    "meeting_date": "2026-08-23T10:00:00Z",
    "participants": ["Rahul Kumar", "Priya Sharma"]
  }'
```

**Response (201 Created):**

```json
{
  "meeting_id": "3f2e1d0c-...",
  "status": "ingested"
}
```

---

### `GET /api/v1/meetings/{meeting_id}` — Retrieve a meeting

```bash
curl http://localhost:8000/api/v1/meetings/3f2e1d0c-...
```

**Response (200 OK):**

```json
{
  "meeting_id": "3f2e1d0c-...",
  "title": "Payment Integration Weekly Sync",
  "transcript": "Rahul reported that the payment provider API is still unstable. Priya asked him to investigate the issue before Friday.",
  "meeting_date": "2026-08-23T10:00:00Z",
  "participants": ["Rahul Kumar", "Priya Sharma"],
  "ingested_at": "2026-08-23T17:30:00Z"
}
```

If the meeting does not exist, the API returns `404 Not Found`.

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Project Structure

```
app/
├── main.py                         # FastAPI application entry point
├── api/
│   └── meetings.py                 # HTTP routing for meetings
├── models/
│   └── meeting.py                  # Internal domain model
├── schemas/
│   └── meeting.py                  # API request / response schemas
├── services/
│   └── meeting_service.py          # Business logic
├── repositories/
│   └── meeting_repository.py       # Storage abstraction + in-memory impl
└── core/
    └── config.py                   # Application settings

tests/
└── test_meetings.py                # API integration tests
```

---

## Architecture Notes

- **Separation of concerns:** `API → Service → Repository → Storage`. No layer leaks into another.
- **Repository abstraction:** `AbstractMeetingRepository` defines the storage contract. Swap in PostgreSQL later by implementing the same interface — the service layer does not change.
- **Domain model vs API schema:** `app/models/meeting.py` is the internal model; `app/schemas/meeting.py` is the public API contract. Future pipeline fields (extraction results, entity mentions, etc.) will be added to the domain model without breaking the API.
- **Storage today:** Simple in-memory dictionary. Suitable for development and testing only.
