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

**Today's implementation** covers the first two stages: meeting ingestion/retrieval, and evidence-backed information extraction.

---

## Information Extraction

The extraction pipeline analyses a stored meeting transcript and returns structured, evidence-backed organisational facts.

It answers: **"What was explicitly said in this meeting?"**

It deliberately does **not** perform:
- Inference or reasoning beyond what was stated
- Entity resolution or cross-meeting correlation
- Risk prediction (only explicitly stated risks are extracted)

### What gets extracted

| Category | Description |
|---|---|
| **Issues** | Problems, blockers, or errors explicitly reported |
| **Tasks** | Action items with optional owner and deadline |
| **Decisions** | Agreements or conclusions reached by the group |
| **Risks** | Concerns or risks explicitly raised |

Every extracted item includes **supporting evidence** — the verbatim transcript text that supports the extraction.

### Extraction rules

- Extract only information directly supported by the transcript.
- Use `null` for optional fields (owner, deadline, severity) when not explicitly mentioned.
- Preserve conditional language (e.g. "if the issue is not resolved").
- Prefer no extraction over a hallucinated one.

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

### 3. Configure the LLM provider

Threadline uses OpenAI GPT-4o for extraction by default.

Create a `.env` file in the project root:

```env
# Required for real extractions
OPENAI_API_KEY=sk-...your-key-here...

# Optional overrides (defaults shown)
OPENAI_MODEL=gpt-4o
EXTRACTION_PROVIDER=openai
```

> **No API key?** Set `EXTRACTION_PROVIDER=fake` to run the server and test the endpoint structure without making any real LLM calls. The fake provider returns an empty but valid extraction result.

---

### 4. Run the application

```bash
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`.

---

### 5. Explore the API documentation

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
    "transcript": "Rahul reported that the payment provider API is still unstable. Priya asked him to investigate the issue before Friday. The team agreed to delay the release if the issue is not resolved.",
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
  "transcript": "Rahul reported that the payment provider API is still unstable...",
  "meeting_date": "2026-08-23T10:00:00Z",
  "participants": ["Rahul Kumar", "Priya Sharma"],
  "ingested_at": "2026-08-23T17:30:00Z"
}
```

If the meeting does not exist, the API returns `404 Not Found`.

---

### `POST /api/v1/meetings/{meeting_id}/extract` — Extract structured facts

Triggers the information extraction pipeline on a stored meeting.

```bash
curl -X POST http://localhost:8000/api/v1/meetings/3f2e1d0c-.../extract
```

**Response (200 OK):**

```json
{
  "meeting_id": "3f2e1d0c-...",
  "extracted_at": "2026-08-23T17:35:00Z",
  "issues": [
    {
      "description": "Payment provider API is unstable.",
      "evidence": {
        "source_text": "Rahul reported that the payment provider API is still unstable."
      }
    }
  ],
  "tasks": [
    {
      "description": "Investigate the payment provider issue.",
      "owner": "Rahul",
      "deadline": "Friday",
      "evidence": {
        "source_text": "Priya asked him to investigate the issue before Friday."
      }
    }
  ],
  "decisions": [
    {
      "description": "Delay the release if the issue is not resolved.",
      "evidence": {
        "source_text": "The team agreed to delay the release if the issue is not resolved."
      }
    }
  ],
  "risks": []
}
```

**Error responses:**

| Code | Cause |
|---|---|
| `404` | Meeting not found — ingest it first |
| `503` | OpenAI API key missing or provider unavailable |
| `502` | Provider returned a response that failed Threadline's schema validation |

---

## Running Tests

```bash
# Run all tests (no LLM key required — extraction tests use a fake provider)
pytest tests/ -v

# Run only extraction tests
pytest tests/test_extraction.py -v

# Run only meeting ingestion tests
pytest tests/test_meetings.py -v
```

---

## Project Structure

```
app/
├── main.py                              # FastAPI application entry point
├── api/
│   └── meetings.py                      # HTTP routing for meetings + extraction
├── models/
│   ├── meeting.py                       # Internal meeting domain model
│   └── extraction.py                    # Internal extraction domain models
├── schemas/
│   ├── meeting.py                       # Meeting API request/response schemas
│   └── extraction.py                    # Extraction API response schema
├── services/
│   ├── meeting_service.py               # Meeting business logic
│   └── extraction_service.py            # Extraction orchestration
├── repositories/
│   ├── meeting_repository.py            # Meeting storage abstraction
│   └── extraction_repository.py         # Extraction result storage abstraction
├── extraction/
│   ├── base.py                          # AbstractExtractionProvider interface
│   ├── prompts.py                       # Extraction prompt template
│   ├── openai_provider.py               # OpenAI GPT-4o implementation
│   └── fake_provider.py                 # Deterministic test double
└── core/
    └── config.py                        # Application settings

tests/
├── test_meetings.py                     # Meeting ingestion/retrieval tests
└── test_extraction.py                   # Extraction pipeline tests
```

---

## Architecture Notes

- **Separation of concerns:** `API → Service → Repository → Storage`. No layer leaks into another.
- **Provider abstraction:** `AbstractExtractionProvider` defines the LLM interface. Swap to Gemini, Anthropic, or a local model by implementing one class and updating one env var.
- **Testability:** `FakeExtractionProvider` makes all extraction tests deterministic with no LLM calls.
- **Repository abstraction:** `AbstractMeetingRepository` and `AbstractExtractionRepository` define storage contracts. Swap in PostgreSQL by implementing the same interfaces — service layers do not change.
- **Domain model vs API schema:** Internal models (`app/models/`) evolve freely; public schemas (`app/schemas/`) remain the stable API contract.
- **Error hierarchy:** `ExtractionProviderNotConfiguredError` → 503, `ExtractionProviderResponseError` → 502, `ExtractionError` → 503. All mapped explicitly in the router.
- **Storage today:** Simple in-memory dictionaries. Suitable for development and testing only.

