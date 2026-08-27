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

**Today's implementation** covers the first three stages: meeting ingestion/retrieval, evidence-backed information extraction, and the Entity Resolution Foundation (canonical entity registry + mention tracking).

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

## Entity Resolution

The entity resolution system separates **observed references** (mentions) from **canonical organisational objects** (entities).

It answers: **"Who or what is this transcript actually referring to?"**

### The core distinction

```
Mention                   Canonical Entity
───────                   ────────────────
"Rahul"         ──────►   PERSON · Rahul Kumar
"Rahul Kumar"   ──────►   PERSON · Rahul Kumar
"R. Kumar"      ──────►   PERSON · Rahul Kumar

"the backend lead"        (no match → UNRESOLVED)
```

### Entity types

| Type | Description |
|---|---|
| **PERSON** | An individual referenced in meeting transcripts |
| **ISSUE** | A specific problem or blocker being tracked |

Additional types (TEAM, PROJECT, SYSTEM, INITIATIVE) are planned for future iterations.

### Resolution policy (today)

Resolution is **deliberately conservative** — exact match only.

A mention resolves to a canonical entity if and only if its text matches (case-insensitively, after whitespace normalisation) the entity's canonical name or one of its aliases, within the same entity type.

| Mention text | Canonical entity | Result |
|---|---|---|
| `"Rahul Kumar"` | PERSON · "Rahul Kumar" | ✅ RESOLVED |
| `"  RAHUL  KUMAR  "` | PERSON · "Rahul Kumar" | ✅ RESOLVED (normalised) |
| `"Rahul"` (alias added) | PERSON · "Rahul Kumar" | ✅ RESOLVED |
| `"Rahul"` (no alias) | PERSON · "Rahul Kumar" | ❌ UNRESOLVED |
| `"the backend lead"` | *(any)* | ❌ UNRESOLVED |

Unresolved mentions are stored with `entity_id: null`. A new entity is **never** automatically created for an unresolved mention — that would risk incorrect merges.

Fuzzy matching, embeddings, and LLM-based resolution are explicitly out of scope for the current version and will be introduced incrementally.

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

### `POST /api/v1/entities` — Create a canonical entity

```bash
curl -X POST http://localhost:8000/api/v1/entities \
  -H "Content-Type: application/json" \
  -d '{"entity_type": "PERSON", "canonical_name": "Rahul Kumar"}'
```

**Response (200 OK):**

```json
{
  "entity_id": "e1a2b3c4-...",
  "entity_type": "PERSON",
  "canonical_name": "rahul kumar",
  "aliases": [],
  "created_at": "2026-08-23T17:40:00Z"
}
```

If an entity of the same type with the same name already exists, the existing entity is returned (not a duplicate).

---

### `GET /api/v1/entities` — List entities

```bash
# All entities
curl http://localhost:8000/api/v1/entities

# Filter by type
curl "http://localhost:8000/api/v1/entities?entity_type=PERSON"
```

---

### `GET /api/v1/entities/{entity_id}` — Retrieve an entity

```bash
curl http://localhost:8000/api/v1/entities/e1a2b3c4-...
```

Returns `404 Not Found` if the entity does not exist.

---

### `POST /api/v1/entities/mentions` — Register a mention

Register an observed reference from a transcript and attempt exact-match resolution.

```bash
curl -X POST http://localhost:8000/api/v1/entities/mentions \
  -H "Content-Type: application/json" \
  -d '{
    "entity_type": "PERSON",
    "text": "Rahul Kumar",
    "meeting_id": "3f2e1d0c-...",
    "source_text": "Rahul Kumar reported the API issue."
  }'
```

**Response — RESOLVED (201 Created):**

```json
{
  "mention_id": "m1a2b3c4-...",
  "text": "Rahul Kumar",
  "entity_type": "PERSON",
  "entity_id": "e1a2b3c4-...",
  "resolution_status": "RESOLVED",
  "created_at": "2026-08-23T17:41:00Z"
}
```

**Response — UNRESOLVED (201 Created):**

```json
{
  "mention_id": "m9z8y7x6-...",
  "text": "the backend lead",
  "entity_type": "PERSON",
  "entity_id": null,
  "resolution_status": "UNRESOLVED",
  "created_at": "2026-08-23T17:41:01Z"
}
```

---

## Running Tests

```bash
# Run all tests (no LLM key required — extraction tests use a fake provider)
pytest tests/ -v

# Run only entity resolution tests
pytest tests/test_entities.py -v

# Run only extraction tests
pytest tests/test_extraction.py -v

# Run only meeting ingestion tests
pytest tests/test_meetings.py -v
```

All tests are fully deterministic — no LLM calls, no network access, no database required.

---

## Project Structure

```
app/
├── main.py                              # FastAPI application entry point
├── api/
│   ├── meetings.py                      # HTTP routing for meetings + extraction
│   └── entities.py                      # HTTP routing for entity registry + mentions
├── models/
│   ├── meeting.py                       # Internal meeting domain model
│   ├── extraction.py                    # Internal extraction domain models
│   └── entity.py                        # CanonicalEntity, EntityMention, EntityType, ResolutionStatus
├── schemas/
│   ├── meeting.py                       # Meeting API request/response schemas
│   ├── extraction.py                    # Extraction API response schema
│   └── entity.py                        # Entity registry API schemas
├── services/
│   ├── meeting_service.py               # Meeting business logic
│   ├── extraction_service.py            # Extraction orchestration
│   └── entity_service.py               # Entity creation + mention registration
├── repositories/
│   ├── meeting_repository.py            # Meeting storage abstraction
│   ├── extraction_repository.py         # Extraction result storage abstraction
│   ├── entity_repository.py             # Canonical entity storage + exact-match lookup
│   └── mention_repository.py            # Entity mention storage abstraction
├── extraction/
│   ├── base.py                          # AbstractExtractionProvider interface
│   ├── prompts.py                       # Extraction prompt template
│   ├── openai_provider.py               # OpenAI GPT-4o implementation
│   └── fake_provider.py                 # Deterministic test double
└── core/
    └── config.py                        # Application settings

tests/
├── test_meetings.py                     # Meeting ingestion/retrieval tests
├── test_extraction.py                   # Extraction pipeline tests
└── test_entities.py                     # Entity registry + mention resolution tests
```

---

## Architecture Notes

- **Separation of concerns:** `API → Service → Repository → Storage`. No layer leaks into another.
- **Provider abstraction:** `AbstractExtractionProvider` defines the LLM interface. Swap to Gemini, Anthropic, or a local model by implementing one class and updating one env var.
- **Testability:** `FakeExtractionProvider` makes all extraction tests deterministic with no LLM calls. Entity tests use isolated in-memory repositories via FastAPI dependency overrides.
- **Repository abstraction:** All repositories (`AbstractMeetingRepository`, `AbstractExtractionRepository`, `AbstractEntityRepository`, `AbstractMentionRepository`) define storage contracts. Swap in PostgreSQL by implementing the same interfaces — service layers do not change.
- **Domain model vs API schema:** Internal models (`app/models/`) evolve freely; public schemas (`app/schemas/`) remain the stable API contract.
- **Error hierarchy:** `ExtractionProviderNotConfiguredError` → 503, `ExtractionProviderResponseError` → 502, `ExtractionError` → 503, `EntityNotFoundError` → 404. All mapped explicitly in their respective routers.
- **Normalisation contract:** The `_normalize()` function (strip, collapse spaces, lowercase) is defined once in `entity_repository.py` and imported by `entity_service.py`. The service and repository always agree on what constitutes an exact match.
- **Unresolved mentions are first-class:** `entity_id: null` on a mention is a meaningful data state, not an error. The system never auto-creates an entity for an unresolved mention.
- **Storage today:** Simple in-memory dictionaries. Suitable for development and testing only.

