# Threadline

> **Threadline** transforms meeting data into structured organisational memory, insights, and proactive risk intelligence.

---

## What is Threadline?

Threadline is an evidence-backed AI platform that ingests meeting transcripts and builds a persistent, queryable record of your organisation's decisions, commitments, risks, and context.

The long-term intelligence pipeline is:

```
Meeting Ingestion → Information Extraction → Entity Resolution
    → Candidate Generation → Candidate Scoring → Resolution Decision
    → Cross-Meeting Correlation → Temporal State Engine
    → Organisational Memory → Retrieval & AI Reasoning
    → Proactive Intelligence
```

**Today's implementation** covers the first four stages: meeting ingestion/retrieval, evidence-backed information extraction, the Entity Resolution Foundation (canonical entity registry + mention tracking), and Candidate Generation (lexical shortlisting of candidate entities for unresolved mentions).

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

## Candidate Generation

Candidate generation is the **next stage** after exact-match resolution.

It answers: **"Given an unresolved mention, which canonical entities are plausible candidates worth evaluating later?"**

This is deliberately **not** final entity resolution.  Its job is high-recall triage, not a decision.

### Why separate from exact-match resolution?

Exact-match resolution is binary — a mention either matches a canonical entity precisely or it doesn't.  Many real mentions ("Rahul", "payment API") are ambiguous or abbreviated; they won't exact-match but they're not meaningless.

Candidate generation bridges the gap by surfacing a **shortlist** of entities worth evaluating.  A future scoring stage will decide which (if any) is the correct match.

### Why prioritise recall over precision?

It is acceptable to include a candidate that turns out to be wrong.  It is **not** acceptable to exclude the correct entity from the shortlist — that would make correct resolution impossible downstream.

A candidate is not a prediction.  It is a suggestion.

### Today's implementation — lexical token overlap

```
"Rahul"
    ↓
Candidate Generation (token overlap)
    ↓
["Rahul Kumar", "Rahul Sharma"]
    ↓
No decision yet — awaiting future scoring stage
```

**Algorithm:**

1. **Normalize** the mention text (strip, lowercase, collapse whitespace).
2. **Tokenize** into a set of words; single-character tokens are excluded.
3. For each entity of the **same type**, compute the overlap between mention tokens and the entity's canonical_name + aliases tokens.
4. Entities with overlap ≥ 1 become candidates.
5. **Order** by: most overlapping tokens first → alphabetical name → entity_id.

**Invariants (always hold):**

- Candidate generation **never** resolves a mention (never assigns `entity_id`).
- Candidate generation **never** changes `resolution_status`.
- Candidate generation only compares entities of the **same entity type**.
- Results are always **deterministic** — same inputs → same ordered output.
- Candidate generation is **read-only** with respect to resolution state.

### Calling the candidates endpoint

```bash
curl http://localhost:8000/api/v1/entities/mentions/{mention_id}/candidates
```

**Response — UNRESOLVED mention with candidates:**

```json
{
  "mention_id": "m_001",
  "resolution_status": "UNRESOLVED",
  "candidates": [
    {
      "entity_id": "entity_001",
      "entity_type": "PERSON",
      "canonical_name": "rahul kumar",
      "candidate_reason": "lexical_token_overlap"
    },
    {
      "entity_id": "entity_002",
      "entity_type": "PERSON",
      "canonical_name": "rahul sharma",
      "candidate_reason": "lexical_token_overlap"
    }
  ]
}
```

**Response — RESOLVED mention (empty list):**

```json
{
  "mention_id": "m_002",
  "resolution_status": "RESOLVED",
  "candidates": []
}
```

`404` is returned if the mention does not exist.

---

## Candidate Scoring

Candidate scoring is the **third stage** of entity resolution, operating after Candidate Generation.

It answers: **"Given an unresolved mention and a shortlist of candidate entities, how strong is the lexical evidence for each candidate?"**

This is deliberately **not** final entity resolution. Its job is to evaluate and rank, not to decide.

### Algorithm (Lexical Weighted Coverage)

The lexical scorer evaluates candidates using a deterministic, highly explainable token-overlap formula:

1. **Exact Match Check**: If the normalised mention exactly equals the candidate's canonical name or one of its aliases, `score = 1.0`.
2. **Component Scores**: Otherwise, it computes token overlap coverage for both the mention and the candidate representation:
   - `mention_coverage` = matched tokens / total mention tokens
   - `candidate_coverage` = matched tokens / total candidate tokens
3. **Combined Score**: `score = 0.6 × mention_coverage + 0.4 × candidate_coverage`.
4. **Best Representation Wins**: Every alias is scored independently. The best score across the canonical name and all aliases is selected.

### Invariants (always hold):

- Candidate scoring **never** resolves a mention (never assigns `entity_id`).
- Candidate scoring **never** changes `resolution_status`.
- Candidate scoring **never** creates canonical entities.
- It only processes candidates provided by the generation stage.
- Results are always **deterministic** and **explainable** via component scores.

### Calling the scored candidates endpoint

```bash
curl http://localhost:8000/api/v1/entities/mentions/{mention_id}/scored-candidates
```

**Response — UNRESOLVED mention with scored candidates:**

```json
{
  "mention_id": "m_001",
  "resolution_status": "UNRESOLVED",
  "candidates": [
    {
      "entity_id": "entity_001",
      "canonical_name": "rahul kumar",
      "score": 1.0,
      "scoring_method": "lexical_weighted_coverage",
      "matched_representation": "rahul kumar",
      "mention_coverage": 1.0,
      "candidate_coverage": 1.0,
      "exact_match": true
    },
    {
      "entity_id": "entity_002",
      "canonical_name": "rahul sharma",
      "score": 0.6,
      "scoring_method": "lexical_weighted_coverage",
      "matched_representation": "rahul sharma",
      "mention_coverage": 1.0,
      "candidate_coverage": 0.5,
      "exact_match": false
    }
  ]
}
```

**Response — RESOLVED mention (empty list):**

```json
{
  "mention_id": "m_002",
  "resolution_status": "RESOLVED",
  "candidates": []
}
```

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

### `GET /api/v1/entities/mentions/{mention_id}/candidates` — Candidate generation

Return an ordered list of candidate canonical entities for an unresolved mention.

```bash
curl http://localhost:8000/api/v1/entities/mentions/m_001.../candidates
```

**Response (200 OK — unresolved mention with candidates):**

```json
{
  "mention_id": "m_001",
  "resolution_status": "UNRESOLVED",
  "candidates": [
    {
      "entity_id": "entity_001",
      "entity_type": "PERSON",
      "canonical_name": "rahul kumar",
      "candidate_reason": "lexical_token_overlap"
    }
  ]
}
```

Always returns `200`.  Returns `404` only if the mention does not exist.

---


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
│   └── entities.py                      # HTTP routing for entity registry + mentions + candidates
├── models/
│   ├── meeting.py                       # Internal meeting domain model
│   ├── extraction.py                    # Internal extraction domain models
│   └── entity.py                        # CanonicalEntity, EntityMention, EntityCandidate, EntityType, ResolutionStatus
├── schemas/
│   ├── meeting.py                       # Meeting API request/response schemas
│   ├── extraction.py                    # Extraction API response schema
│   └── entity.py                        # Entity registry + candidate API schemas
├── services/
│   ├── meeting_service.py               # Meeting business logic
│   ├── extraction_service.py            # Extraction orchestration
│   ├── entity_service.py               # Entity creation + mention registration
│   └── candidate_service.py            # Candidate generation orchestration
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
├── entity_resolution/
│   ├── base.py                          # AbstractCandidateGenerator interface
│   └── lexical_candidate_generator.py   # Token-overlap implementation
└── core/
    └── config.py                        # Application settings

tests/
├── test_meetings.py                     # Meeting ingestion/retrieval tests
├── test_extraction.py                   # Extraction pipeline tests
├── test_entities.py                     # Entity registry + mention resolution tests
└── test_candidates.py                   # Candidate generation tests
```

---

## Architecture Notes

- **Separation of concerns:** `API → Service → Repository → Storage`. No layer leaks into another.
- **Provider abstraction:** `AbstractExtractionProvider` defines the LLM interface. Swap to Gemini, Anthropic, or a local model by implementing one class and updating one env var.
- **Generator abstraction:** `AbstractCandidateGenerator` defines the candidate generation interface. Swap `LexicalCandidateGenerator` for an embedding-based or contextual generator without changing the service layer.
- **Testability:** `FakeExtractionProvider` makes all extraction tests deterministic with no LLM calls. Entity and candidate tests use isolated in-memory repositories via FastAPI dependency overrides.
- **Repository abstraction:** All repositories (`AbstractMeetingRepository`, `AbstractExtractionRepository`, `AbstractEntityRepository`, `AbstractMentionRepository`) define storage contracts. Swap in PostgreSQL by implementing the same interfaces — service layers do not change.
- **Domain model vs API schema:** Internal models (`app/models/`) evolve freely; public schemas (`app/schemas/`) remain the stable API contract.
- **Error hierarchy:** `ExtractionProviderNotConfiguredError` → 503, `ExtractionProviderResponseError` → 502, `ExtractionError` → 503, `EntityNotFoundError` → 404, `MentionNotFoundError` → 404. All mapped explicitly in their respective routers.
- **Normalisation contract:** The `_normalize()` function (strip, collapse spaces, lowercase) is defined once in `entity_repository.py` and imported by `entity_service.py` and `lexical_candidate_generator.py`. All three layers agree on what constitutes an exact or lexical match.
- **Unresolved mentions are first-class:** `entity_id: null` on a mention is a meaningful data state, not an error. The system never auto-creates an entity for an unresolved mention.
- **Candidate generation is read-only:** `CandidateService.get_candidates()` never modifies `resolution_status` or `entity_id`. Candidates are suggestions, not decisions.
- **Storage today:** Simple in-memory dictionaries. Suitable for development and testing only.

