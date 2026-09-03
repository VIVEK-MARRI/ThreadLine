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
    → Organisational Memory → Insight & Change Detection
    → Prioritization & Attention Engine → Retrieval & AI Reasoning
    → Proactive Intelligence
```

**Today's implementation** covers the first ten stages: meeting ingestion/retrieval, evidence-backed information extraction, the Entity Resolution Foundation (canonical entity registry + mention tracking), Candidate Generation (lexical shortlisting), Candidate Scoring (explainable lexical evaluation), the **Resolution Decision Engine** (deterministic, safe resolution with explicit RESOLVED / AMBIGUOUS / UNRESOLVED outcomes), **Cross-Meeting Correlation** (read-only aggregation of a resolved entity's history across meetings), the **Temporal State Engine** (deterministic, evidence-backed lifecycle state tracking across time), **Organisational Memory** (deterministic read-only aggregation of an entity's complete structured knowledge and history), the **Insight & Change Detection Engine** (read-only derivation of actionable changes and risks), and the **Prioritization & Attention Engine** (read-only aggregation of signals to identify critical entities).

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

## Resolution Decision

The Resolution Decision Engine is **Stage 4** of the entity-resolution pipeline, operating after Candidate Scoring.

It answers: **"Do we have enough evidence to act on the scored candidates?"**

This is the only stage that may assign an `entity_id` to a mention or change its `resolution_status`.  Candidate generation and scoring are read-only.

### The three pipeline questions

| Stage | Question |
|---|---|
| Candidate Generation | "Find possibilities." |
| Candidate Scoring | "Rank possibilities." |
| Resolution Decision | "Determine whether evidence is sufficient to act." |

### Why a score is NOT a decision

A lexical similarity score of `0.92` means the candidate received a score of 0.92 under the scoring function.  It does **not** mean there is a 92% probability the entity is correct.

The decision engine applies an explicit policy to determine whether the evidence clears a threshold **and** is clearly distinguishable from alternatives.  The highest score does not automatically win — it must also have sufficient margin over the second candidate.

This separation prevents the scoring layer from making uncontrolled resolution decisions.

### The three possible outcomes

| Outcome | Meaning | `entity_id` |
|---|---|---|
| **RESOLVED** | Top candidate exceeded the confidence threshold and had sufficient margin. The mention is matched to the entity. | Set to the selected entity |
| **AMBIGUOUS** | Top candidate exceeded the threshold but was too close to the second candidate.  The system abstains safely. | `null` |
| **UNRESOLVED** | No candidate exceeded the confidence threshold.  The system abstains safely. | `null` |

### Safe abstention

When the evidence is insufficient, the system abstains rather than guessing.  An incorrect entity assignment creates a misleading organisational memory that is hard to fix.  An unresolved or ambiguous mention is easy to resolve later with more information.

### Decision policy — Threshold + Margin

The engine uses a deterministic threshold + margin policy:

| Case | Condition | Decision |
|---|---|---|
| **A** — No candidates | Candidate list is empty | UNRESOLVED |
| **B** — Below threshold | `top_score < resolution_threshold` | UNRESOLVED |
| **C** — Ambiguous gap | `top_score ≥ threshold` AND `margin < ambiguity_margin` | AMBIGUOUS |
| **D** — Clear winner | `top_score ≥ threshold` AND `margin ≥ ambiguity_margin` | RESOLVED |

**Default thresholds:**
- `resolution_threshold = 0.85`
- `ambiguity_margin = 0.10`

**Single-candidate rule:** when only one candidate exists, the margin is treated as infinite — if it meets the threshold it always resolves (there is no second candidate to be ambiguous with).

### Decision invariants (always hold)

1. **RESOLVED never becomes UNRESOLVED.**
2. **RESOLVED never becomes AMBIGUOUS.**
3. UNRESOLVED may become RESOLVED only via an explicit decision.
4. AMBIGUOUS mentions always have `entity_id = null`.
5. UNRESOLVED mentions always have `entity_id = null`.
6. Resolution **never creates a new canonical entity**.
7. Resolution only selects an entity that appeared in the scored candidate list.
8. The decision is **deterministic** — identical inputs always produce identical decisions.

### Example decisions

**Example 1 — RESOLVED:**

```
Candidate scores:
  Rahul Kumar  → 0.94
  Rahul Sharma → 0.40

Margin: 0.94 − 0.40 = 0.54  ≥ ambiguity_margin (0.10)
Top score: 0.94 ≥ threshold (0.85)

Decision: RESOLVED → entity_id = Rahul Kumar
Reason:   "Top candidate exceeded the confidence threshold (0.9400 ≥ 0.8500)
           and had sufficient margin over the second candidate (0.5400 ≥ 0.1000)."
```

**Example 2 — AMBIGUOUS:**

```
Candidate scores:
  Rahul Kumar  → 0.91
  Rahul Sharma → 0.90

Margin: 0.91 − 0.90 = 0.01  < ambiguity_margin (0.10)
Top score: 0.91 ≥ threshold (0.85)

Decision: AMBIGUOUS → entity_id = null
Reason:   "Top candidate exceeded the confidence threshold (0.9100 ≥ 0.8500)
           but was too close to the second candidate (margin 0.0100 < 0.1000)."
```

**Example 3 — UNRESOLVED:**

```
Candidate scores:
  Rahul Kumar → 0.55

Top score: 0.55 < threshold (0.85)

Decision: UNRESOLVED → entity_id = null
Reason:   "No candidate exceeded the confidence threshold
           (top score 0.5500 < threshold 0.8500)."
```

### Calling the resolution endpoint

```bash
curl -X POST http://localhost:8000/api/v1/entities/mentions/{mention_id}/resolve
```

**Response — RESOLVED:**

```json
{
  "mention_id": "m_001",
  "outcome": "RESOLVED",
  "selected_entity_id": "entity_001",
  "top_score": 0.94,
  "second_score": 0.40,
  "score_margin": 0.54,
  "reason": "Top candidate exceeded the confidence threshold (0.9400 >= 0.8500) and had sufficient margin over the second candidate (margin 0.5400 >= 0.1000)."
}
```

**Response — AMBIGUOUS:**

```json
{
  "mention_id": "m_002",
  "outcome": "AMBIGUOUS",
  "selected_entity_id": null,
  "top_score": 0.91,
  "second_score": 0.90,
  "score_margin": 0.01,
  "reason": "Top candidate exceeded the confidence threshold but was too close to the second candidate."
}
```

**Response — UNRESOLVED:**

```json
{
  "mention_id": "m_003",
  "outcome": "UNRESOLVED",
  "selected_entity_id": null,
  "top_score": 0.55,
  "second_score": null,
  "score_margin": null,
  "reason": "No candidate exceeded the confidence threshold (top score 0.5500 < threshold 0.8500)."
}
```

`404` is returned if the mention does not exist.

> **Idempotency:** calling `/resolve` on an already-RESOLVED mention returns the current RESOLVED state without modification.  RESOLVED mentions are never downgraded.

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

### `GET /api/v1/entities/mentions/{mention_id}/scored-candidates` — Candidate scoring

Return a scored, ranked list of candidates.  Read-only; never modifies the mention.

```bash
curl http://localhost:8000/api/v1/entities/mentions/m_001.../scored-candidates
```

Always returns `200`.  Returns `404` only if the mention does not exist.

---

### `POST /api/v1/entities/mentions/{mention_id}/resolve` — Resolution Decision

Apply the Resolution Decision Engine to an unresolved mention.

```bash
curl -X POST http://localhost:8000/api/v1/entities/mentions/m_001.../resolve
```

**Response (200 OK — RESOLVED):**

```json
{
  "mention_id": "m_001",
  "outcome": "RESOLVED",
  "selected_entity_id": "entity_001",
  "top_score": 0.94,
  "second_score": 0.40,
  "score_margin": 0.54,
  "reason": "Top candidate exceeded the confidence threshold..."
}
```

Returns `404` if the mention does not exist.

---

```
# Run all tests (no LLM key required — extraction tests use a fake provider)
pytest tests/ -v

# Run only entity resolution tests
pytest tests/test_entities.py -v

# Run only extraction tests
pytest tests/test_extraction.py -v

# Run only meeting ingestion tests
pytest tests/test_meetings.py -v

# Run only resolution decision tests
pytest tests/test_resolution.py -v

# Run only correlation tests
pytest tests/test_correlation.py -v

# Run only temporal state engine tests
pytest tests/test_temporal.py -v

# Run only organisational memory tests
pytest tests/test_memory.py -v
```

All tests are fully deterministic — no LLM calls, no network access, no database required.

---

## Temporal State Engine

The Temporal State Engine is the seventh stage of the pipeline, operating after Cross-Meeting Correlation.

It answers a fundamentally different question:

| Stage | Question answered |
|---|---|
| **Cross-Meeting Correlation** | *"What observations involving this entity exist across meetings?"* |
| **Temporal State Engine** | *"How does the lifecycle state of this entity evolve over time based on chronological observations?"* |

### Why they are separate

Correlation aggregates raw observations. The Temporal State Engine interprets them for lifecycle state and enforces valid state transitions. Mixing the two would make each harder to test and audit independently.

The Temporal State Engine is **read-only** — it never creates entities, re-runs resolution, triggers candidate generation or scoring, or modifies any mention's `entity_id` or `resolution_status`.

### State vocabulary

| State | Meaning |
|---|---|
| `UNKNOWN` | No state-bearing evidence found |
| `OPEN` | Issue raised/identified but not yet started |
| `IN_PROGRESS` | Actively being worked on |
| `BLOCKED` | Blocked, stalled, or waiting on an external dependency |
| `RESOLVED` | Completed, fixed, or closed |

States apply to any entity type (ISSUE, PERSON, etc.) but are most meaningful for ISSUE entities. PERSON entities with no lifecycle keywords return `UNKNOWN`.

### State interpretation (KeywordStateInterpreter)

Lifecycle state is inferred from the `source_text` of each resolved mention using a deterministic **keyword-based priority scanner** — no LLM is used.

**Priority order (highest wins when multiple keywords co-occur):**

1. **RESOLVED** — `resolved`, `fixed`, `closed`, `completed`, `done`, `finished`
2. **BLOCKED** — `blocked`, `blocker`, `stuck`, `stalled`, `waiting`
3. **IN_PROGRESS** — `started`, `working on`, `in progress`, `in-progress`, `ongoing`, `underway`
4. **OPEN** — `raised`, `identified`, `reported`, `created`, `new issue`, `filed`

If no keyword is found, the state is `UNKNOWN` — observations still appear in the timeline but do not trigger a transition.

**Priority example:** `"The blocked issue has been resolved."` → `RESOLVED` (beats `BLOCKED`).

**Case-insensitive:** `"RESOLVED"` and `"Resolved"` both match.

### Transition policy (DefaultTransitionPolicy)

Only valid state transitions are applied. The policy is deterministic, evidence-backed, and always returns a `TransitionResult` (frozen dataclass).

| Case | Condition | Action |
|---|---|---|
| **A** — UNKNOWN new state | Interpreter returns UNKNOWN | No-op (observation recorded, state unchanged) |
| **B** — Repeated state | `new_state == current_state` | No-op (no duplicate transition) |
| **C** — Valid transition | `(current_state, new_state)` in allowed table | Transition applied |
| **D** — Invalid transition | `(current_state, new_state)` not in allowed table | Transition skipped; observation recorded with `is_valid_transition=false` |

**Allowed transitions:**

```
UNKNOWN → OPEN, IN_PROGRESS, BLOCKED, RESOLVED
OPEN    → IN_PROGRESS, BLOCKED, RESOLVED
IN_PROGRESS → BLOCKED, RESOLVED
BLOCKED → IN_PROGRESS, RESOLVED
RESOLVED → (none — terminal state)
```

Invalid transitions (e.g., `RESOLVED → IN_PROGRESS`) are **recorded** in the timeline with `is_valid_transition=false` and a `transition_skipped_reason` explanation, but the `current_state` remains unchanged.

### Resolution safety rules

Only **RESOLVED** mentions participate in the timeline:
- `entity_id != null` AND `resolution_status == RESOLVED`
- AMBIGUOUS and UNRESOLVED mentions are excluded.
- If the mention references a non-existent meeting, it is silently skipped.

### Deterministic ordering

Observations are always processed and returned in a stable, deterministic order:

```
Primary:   meeting_date   ASC  (earliest observations first)
Secondary: meeting_id     ASC  (stable string sort for same-date meetings)
Tertiary:  mention_id     ASC  (handles same entity mentioned twice in one meeting)
```

### Architecture

```
TemporalStateService
    │
    ├── AbstractEntityRepository      (fetch the canonical entity)
    ├── AbstractMentionRepository     (list all resolved mentions for the entity)
    ├── AbstractMeetingRepository     (get meeting title + date for each mention)
    ├── AbstractStateInterpreter      (interpret source_text → TemporalState)
    └── AbstractTemporalStatePolicy   (apply transition rules)
```

All five collaborators are injected via constructor — fully testable, no global state.

### Data flow

```
CANONICAL ENTITY
    ↓
FIND ALL RESOLVED MENTIONS  (filter: entity_id == entity.entity_id AND status == RESOLVED)
    ↓
JOIN WITH THEIR MEETINGS    (skip if meeting not found)
    ↓
ORDER CHRONOLOGICALLY       (meeting_date, meeting_id, mention_id)
    ↓
FOR EACH OBSERVATION:
    interpret source_text → TemporalState (keyword scanner)
    apply transition policy → TransitionResult
    record StateObservation (with full from/to/validity audit trail)
    ↓
RETURN EntityTimeline
```

### API endpoint

```
GET /api/v1/entities/{entity_id}/timeline
```

**Response (200 OK — entity with state-bearing observations):**

```json
{
  "entity_id": "entity_001",
  "canonical_name": "payment api instability",
  "entity_type": "ISSUE",
  "current_state": "RESOLVED",
  "observation_count": 3,
  "transition_count": 3,
  "timeline": [
    {
      "observation_index": 0,
      "meeting_id": "meeting-a",
      "meeting_title": "Sprint Planning",
      "meeting_date": "2026-08-21T10:00:00Z",
      "mention_id": "m_001",
      "evidence_text": "The payment API issue has started being investigated.",
      "interpreted_state": "IN_PROGRESS",
      "transition_occurred": true,
      "from_state": "UNKNOWN",
      "to_state": "IN_PROGRESS",
      "is_valid_transition": true,
      "transition_skipped_reason": null
    },
    {
      "observation_index": 1,
      "meeting_id": "meeting-b",
      "meeting_title": "Weekly Sync",
      "meeting_date": "2026-08-28T10:00:00Z",
      "mention_id": "m_002",
      "evidence_text": "We are blocked on infrastructure access.",
      "interpreted_state": "BLOCKED",
      "transition_occurred": true,
      "from_state": "IN_PROGRESS",
      "to_state": "BLOCKED",
      "is_valid_transition": true,
      "transition_skipped_reason": null
    },
    {
      "observation_index": 2,
      "meeting_id": "meeting-c",
      "meeting_title": "Retrospective",
      "meeting_date": "2026-09-04T10:00:00Z",
      "mention_id": "m_003",
      "evidence_text": "The payment API issue has been resolved.",
      "interpreted_state": "RESOLVED",
      "transition_occurred": true,
      "from_state": "BLOCKED",
      "to_state": "RESOLVED",
      "is_valid_transition": true,
      "transition_skipped_reason": null
    }
  ]
}
```

**Response (200 OK — entity with no resolved mentions):**

```json
{
  "entity_id": "entity_002",
  "canonical_name": "payment api instability",
  "entity_type": "ISSUE",
  "current_state": "UNKNOWN",
  "observation_count": 0,
  "transition_count": 0,
  "timeline": []
}
```

`404` is returned only if the `entity_id` does not exist. An empty timeline is **not** an error.

---

## Cross-Meeting Correlation

Cross-Meeting Correlation is the sixth stage of the pipeline, operating after the Resolution Decision Engine.

It answers a fundamentally different question from entity resolution:

| Stage | Question answered |
|---|---|
| **Entity Resolution** | *"Who or what is this mention referring to?"* |
| **Cross-Meeting Correlation** | *"What observations involving this resolved entity exist across meetings?"* |

### Why they are separate

Entity resolution assigns identity. Cross-meeting correlation retrieves history. Mixing them would violate the single-responsibility principle and make each harder to test and audit independently.

Correlation assumes entity resolution has already happened. It consumes the results — it never re-runs them.

### What it does

Given a canonical entity ID, the correlation layer:

1. Fetches the canonical entity from the registry.
2. Retrieves all mentions linked to that entity by `entity_id`.
3. Filters to **only RESOLVED mentions** — AMBIGUOUS and UNRESOLVED mentions are excluded.
4. Joins each mention with its meeting to retrieve `title` and `meeting_date`.
5. Returns all observations ordered chronologically.

### Example

After entity resolution across three meetings:

```
Meeting A (2026-08-21):
  mention: "Rahul" (RESOLVED → entity_001)
  source:  "Rahul will fix the payment API."

Meeting B (2026-08-28):
  mention: "Rahul Kumar" (RESOLVED → entity_001)
  source:  "Rahul Kumar is still investigating the payment API."

Meeting C (2026-09-04):
  mention: "Rahul" (RESOLVED → entity_001)
  source:  "Rahul said the issue is still open."
```

Correlation returns all three observations as one ordered history for entity_001.

### Resolution safety rules

| Status | entity_id | Participates in correlation? |
|---|---|---|
| RESOLVED | set to canonical entity ID | **Yes** |
| AMBIGUOUS | None | **No** |
| UNRESOLVED | None | **No** |

Correlation never re-runs resolution, never assigns entity_ids, and never creates entities.

### Deterministic ordering

Observations are always returned in a stable, deterministic order:

```
Primary:   meeting_date   ASC  (earliest observations first)
Secondary: meeting_id     ASC  (stable string sort for same-date meetings)
Tertiary:  mention_id     ASC  (handles same entity mentioned twice in one meeting)
```

All three sort keys are real fields from the existing domain models. No timestamps are invented.

### Architecture

```
CorrelationService
    │
    ├── AbstractEntityRepository   (fetch the canonical entity)
    ├── AbstractMentionRepository  (list all mentions by entity_id)
    └── AbstractMeetingRepository  (get meeting title + date for each mention)
```

The service is deliberately separate from `EntityService` and `ResolutionService`:
- `EntityService` owns entity lifecycle and exact-match resolution.
- `ResolutionService` owns resolution decisions.
- `CorrelationService` owns cross-meeting aggregation.

No new repository is introduced. Correlation is computed from three existing repositories.

### Data flow

```
RESOLVED CANONICAL ENTITY
        ↓
FIND ALL RESOLVED MENTIONS  (list_by_entity_id + filter status==RESOLVED)
        ↓
JOIN WITH THEIR MEETINGS    (get_by_id for each mention.meeting_id)
        ↓
ORDER CHRONOLOGICALLY       (meeting_date, meeting_id, mention_id)
        ↓
RETURN EXPLAINABLE CROSS-MEETING HISTORY
```

### API endpoint

```
GET /api/v1/entities/{entity_id}/correlations
```

**Why `correlations` not `history`?**
Existing patterns use noun-plurals (`/entities`, `/mentions`, `/candidates`). `history` implies temporal state tracking (a later stage). `correlations` precisely describes cross-meeting aggregation.

**Response (200 OK — entity with resolved mentions):**

```json
{
  "entity_id": "entity_001",
  "canonical_name": "rahul kumar",
  "entity_type": "PERSON",
  "observation_count": 3,
  "observations": [
    {
      "meeting_id": "meeting-a",
      "meeting_title": "Sprint Planning",
      "meeting_date": "2026-08-21T10:00:00Z",
      "mention_id": "m_001",
      "mention_text": "Rahul",
      "source_text": "Rahul will fix the payment API."
    },
    {
      "meeting_id": "meeting-b",
      "meeting_title": "Weekly Sync",
      "meeting_date": "2026-08-28T10:00:00Z",
      "mention_id": "m_002",
      "mention_text": "Rahul Kumar",
      "source_text": "Rahul Kumar is still investigating the payment API."
    }
  ]
}
```

**Response (200 OK — entity with no resolved mentions):**

```json
{
  "entity_id": "entity_002",
  "canonical_name": "payment api instability",
  "entity_type": "ISSUE",
  "observation_count": 0,
  "observations": []
}
```

`404` is returned only if the `entity_id` does not exist. An empty correlation history is **not** an error.

### What is intentionally NOT implemented

The following capabilities belong to later pipeline stages and are **explicitly excluded** from this implementation:

- Temporal state transitions (e.g. OPEN → IN PROGRESS → RESOLVED)
- Knowledge graph or graph database
- Embeddings / vector search
- LLM-based correlation or inference
- Fuzzy entity matching within correlation
- Automatic inference of relationships between observations
- Risk detection or issue state tracking
- Background workers or async aggregation

---

## Organisational Memory

Organisational Memory is the **eighth stage** of the pipeline, sitting above Cross-Meeting Correlation and the Temporal State Engine.

It answers the ultimate aggregation question:

> **"What does the organisation currently know about this entity based on all available evidence?"**

### Why it exists

While Correlation lists raw observations and Temporal State tracks lifecycle changes, humans need a synthesized, immediately readable summary of facts. The Organisational Memory layer aggregates the underlying timelines and correlations into a set of highly structured, epistemically grounded **memory facts**.

### Memory Fact Types

Every memory fact (except `CURRENT_STATE`) is strictly grounded in evidence, pointing back to a specific meeting and/or mention.

| Fact Type | Meaning |
|---|---|
| **FIRST_OBSERVED** | The earliest resolved observation across all meetings. |
| **LAST_OBSERVED** | The most recent resolved observation (only present if ≥ 2 observations exist). |
| **CURRENT_STATE** | The entity's current lifecycle state (an aggregate fact, no single evidence pointer). |
| **STATE_TRANSITION** | A valid lifecycle state transition that actually occurred. |
| **REPEATED_OBSERVATION** | A meeting in which this entity was observed two or more times. |

Invalid state transitions (those skipped by the Temporal State Engine) are **not** promoted to memory facts.

### Invariants (always hold)

- **Read-only**: The service never creates entities, resolves mentions, or triggers candidate generation.
- **No Hallucination**: No LLMs, embeddings, or heuristic guess-work are used. Every fact traces to existing resolved observations.
- **Deterministic**: Given the same underlying meetings and mentions, the memory representation is identically ordered and constructed.
- **Safe Filtering**: Only `RESOLVED` mentions participate. `UNRESOLVED` and `AMBIGUOUS` mentions are strictly excluded.
- **Chronological Timestamps**: Uses the actual meeting dates (`meeting_date`) for all timestamps, never `datetime.now()`.

### API endpoint

```
GET /api/v1/entities/{entity_id}/memory
```

**Response (200 OK — entity with rich history):**

```json
{
  "entity_id": "entity_001",
  "canonical_name": "payment api instability",
  "entity_type": "ISSUE",
  "first_observed_at": "2026-08-01T10:00:00Z",
  "last_observed_at": "2026-08-22T10:00:00Z",
  "meeting_count": 3,
  "observation_count": 4,
  "current_state": "BLOCKED",
  "facts": [
    {
      "fact_type": "FIRST_OBSERVED",
      "value": "2026-08-01T10:00:00+00:00",
      "source_meeting_id": "meeting_001",
      "source_mention_id": "mention_abc",
      "observed_at": "2026-08-01T10:00:00Z",
      "detail": "Sprint Planning"
    },
    {
      "fact_type": "CURRENT_STATE",
      "value": "BLOCKED",
      "source_meeting_id": null,
      "source_mention_id": null,
      "observed_at": null,
      "detail": null
    },
    {
      "fact_type": "STATE_TRANSITION",
      "value": "UNKNOWN → IN_PROGRESS",
      "source_meeting_id": "meeting_001",
      "source_mention_id": "mention_abc",
      "observed_at": "2026-08-01T10:00:00Z",
      "detail": "Sprint Planning"
    }
  ]
}
```

**Response (200 OK — entity with no resolved mentions):**

```json
{
  "entity_id": "entity_002",
  "canonical_name": "database timeout",
  "entity_type": "ISSUE",
  "first_observed_at": null,
  "last_observed_at": null,
  "meeting_count": 0,
  "observation_count": 0,
  "current_state": "UNKNOWN",
  "facts": [
    {
      "fact_type": "CURRENT_STATE",
      "value": "UNKNOWN",
      "source_meeting_id": null,
      "source_mention_id": null,
      "observed_at": null,
      "detail": null
    }
  ]
}
```

Returns `404` only if the `entity_id` does not exist.

---

## Insight & Change Detection Engine

The Insight & Change Detection Engine is the **ninth stage** of the pipeline, sitting above Organisational Memory and the Temporal State Engine.

It answers the operational question:

> **"What changed for this entity, and which changes are important?"**

### Why it exists

While Organisational Memory provides a static summary of facts, users need to know when meaningful events occur. The Insight Engine reasons over timelines and memory facts to generate actionable intelligence: state changes, blockers, resolution, reopen attempts, and staleness.

### Insight Vocabulary

Every insight generated by this engine maps to one of seven types and carries a deterministic severity.

| Insight Type | Severity | Condition |
|---|---|---|
| **STATE_CHANGED** | INFO | The entity moved from one valid lifecycle state to another. |
| **ISSUE_RESOLVED** | INFO | The entity entered the RESOLVED state (additive with STATE_CHANGED). |
| **ISSUE_BLOCKED** | WARNING | The entity entered the BLOCKED state (additive with STATE_CHANGED). |
| **REOPEN_ATTEMPT** | WARNING | An observation attempted to reopen a RESOLVED entity (invalid transition). |
| **REPEATED_OBSERVATION**| INFO | The entity was observed multiple times in one meeting without a state transition. |
| **UNKNOWN_STATE** | INFO | The entity has observations but no state-bearing keywords were ever found. |
| **STALE_ENTITY** | WARNING | The entity has not been observed for ≥ 30 days and is not RESOLVED. |

### Deduplication and Determinism

The Insight Engine is **strictly deterministic**:
1. It is entirely **read-only** (never modifies entities, mentions, or states).
2. It generates a deterministic `insight_id` via SHA-256 hash of `(entity_id, insight_type, meeting_id, observation_index)`.
3. Running the service multiple times on the same data produces the identical list of insights, in the exact same order.

### API endpoint

```
GET /api/v1/entities/{entity_id}/insights
```

**Response (200 OK):**

```json
{
  "entity_id": "entity_001",
  "insight_count": 2,
  "insights": [
    {
      "insight_id": "a3f9c1d2e4b50617",
      "entity_id": "entity_001",
      "insight_type": "STATE_CHANGED",
      "title": "State changed",
      "description": "The entity transitioned from UNKNOWN to IN_PROGRESS.",
      "severity": "INFO",
      "observed_at": "2026-08-01T10:00:00Z",
      "related_meeting_id": "meeting_001",
      "evidence": "Started working on the payment API issue.",
      "deterministic_sort_key": "2026-08-01T10:00:00+00:00|entity_001|STATE_CHANGED|a3f9c1d2e4b50617"
    },
    {
      "insight_id": "b7e2a0f3c91d4825",
      "entity_id": "entity_001",
      "insight_type": "STALE_ENTITY",
      "title": "Stale entity",
      "description": "Entity 'payment API' has not been observed for 45 day(s) and is not RESOLVED.  Current state: IN_PROGRESS.",
      "severity": "WARNING",
      "observed_at": "2026-08-01T10:00:00Z",
      "related_meeting_id": null,
      "evidence": "Entity last observed at 2026-08-01T10:00:00+00:00, 45 day(s) ago.",
      "deterministic_sort_key": "2026-08-01T10:00:00+00:00|entity_001|STALE_ENTITY|b7e2a0f3c91d4825"
    }
  ]
}
```

---

## Prioritization & Attention Engine

The Prioritization & Attention Engine is the **tenth stage** of the pipeline, sitting above the Insight & Change Detection Engine.

It answers the operational question:

> **"Does the organisation need to pay attention to this entity right now, and if so, why?"**

### Why it exists

While the Insight Engine tells us what changed, organizations need to know what to look at first. The Attention Engine aggregates all applicable signals (e.g. STALE_ENTITY, REOPEN_ATTEMPT, ISSUE_BLOCKED) into a single deterministic score and priority level (CRITICAL, HIGH, MEDIUM, LOW) for an entity.

### Scoring and Deduplication

The engine aggregates insights based on a deterministic scoring system:

1. **Rule F (Deduplication)**: Each reason (e.g., `ENTITY_STALE`) contributes to the score at most once per entity, regardless of how many individual observations triggered it.
2. **Rule E (No Zero-Score Entities)**: If an entity has no actionable signals (`score = 0`), it generates no attention record.
3. **Deterministic Output**: The evaluation is 100% read-only. Calling it multiple times produces the identical result and the exact same `attention_id`.

### Priority Levels

- `CRITICAL`: score ≥ 100
- `HIGH`: 50 ≤ score < 100
- `MEDIUM`: 20 ≤ score < 50
- `LOW`: 0 < score < 20

### API endpoint

```
GET /api/v1/attention
```

**Response (200 OK):**

```json
{
  "entity_count": 1,
  "items": [
    {
      "attention_id": "a1b2c3d4e5f60718",
      "entity_id": "entity_001",
      "attention_level": "CRITICAL",
      "score": 120,
      "reasons": [
        "ENTITY_BLOCKED",
        "RECENT_STATE_CHANGE"
      ],
      "related_insight_ids": [
        "a3f9c1d2e4b50617",
        "b7e2a0f3c91d4825"
      ],
      "evaluated_at": "2026-08-01T10:00:00Z"
    }
  ]
}
```

---

## Project Structure

```
app/
├── main.py                              # FastAPI application entry point
├── api/
│   ├── meetings.py                      # HTTP routing for meetings + extraction
│   ├── entities.py                      # HTTP routing for entities, mentions, candidates, resolution, correlation, timeline
│   └── attention.py                     # HTTP routing for top-level attention aggregation
├── models/
│   ├── meeting.py                       # Internal meeting domain model
│   ├── extraction.py                    # Internal extraction domain models
│   ├── entity.py                        # CanonicalEntity, EntityMention, EntityCandidate,
│   │                                   #   ScoredEntityCandidate, ResolutionDecision,
│   │                                   #   EntityType, ResolutionStatus, ResolutionOutcome
│   ├── correlation.py                   # EntityObservation, EntityCorrelation (read-models)
│   ├── temporal.py                      # TemporalState, StateObservation, EntityTimeline (read-models)
│   ├── memory.py                        # MemoryFactType, EntityMemoryFact, EntityMemory (read-models)
│   ├── insights.py                      # InsightType, InsightSeverity, EntityInsight (read-models)
│   └── attention.py                     # AttentionLevel, AttentionReason, EntityAttention (read-models)
├── schemas/
│   ├── meeting.py                       # Meeting API request/response schemas
│   ├── extraction.py                    # Extraction API response schema
│   ├── entity.py                        # Entity, mention, candidate, scoring, decision schemas
│   ├── correlation.py                   # Cross-meeting correlation API response schema
│   ├── temporal.py                      # Temporal State Engine API response schema
│   ├── memory.py                        # Organisational Memory API response schema
│   ├── insights.py                      # Insight & Change Detection API response schema
│   └── attention.py                     # Prioritization & Attention API response schema
├── services/
│   ├── meeting_service.py               # Meeting business logic
│   ├── extraction_service.py            # Extraction orchestration
│   ├── entity_service.py               # Entity creation + mention registration
│   ├── candidate_service.py            # Candidate generation orchestration
│   ├── candidate_scoring_service.py     # Candidate scoring orchestration
│   ├── resolution_service.py            # Resolution Decision orchestration (Stage 4)
│   ├── correlation_service.py           # Cross-meeting correlation (Stage 5, read-only)
│   ├── temporal_state_service.py        # Temporal State Engine orchestration (Stage 6, read-only)
│   ├── organisational_memory_service.py # Organisational Memory orchestration (Stage 7, read-only)
│   ├── insight_service.py               # Insight & Change Detection orchestration (Stage 8, read-only)
│   └── attention_service.py             # Prioritization & Attention orchestration (Stage 9, read-only)
├── repositories/
│   ├── meeting_repository.py            # Meeting storage abstraction
│   ├── extraction_repository.py         # Extraction result storage abstraction
│   ├── entity_repository.py             # Canonical entity storage + exact-match lookup
│   └── mention_repository.py            # Entity mention storage (with update() method)
├── extraction/
│   ├── base.py                          # AbstractExtractionProvider interface
│   ├── prompts.py                       # Extraction prompt template
│   ├── openai_provider.py               # OpenAI GPT-4o implementation
│   └── fake_provider.py                 # Deterministic test double
├── entity_resolution/
│   ├── base.py                          # AbstractCandidateGenerator interface
│   ├── scoring_base.py                  # AbstractCandidateScorer interface
│   ├── resolution_policy.py             # AbstractResolutionPolicy + ThresholdResolutionPolicy
│   ├── lexical_candidate_generator.py   # Token-overlap candidate generation
│   ├── lexical_candidate_scorer.py      # Weighted coverage scoring
│   └── lexical_utils.py                 # Shared tokenisation utilities
├── temporal/
│   ├── __init__.py                      # Package marker
│   ├── state_interpreter.py             # AbstractStateInterpreter + KeywordStateInterpreter
│   └── transition_policy.py             # AbstractTemporalStatePolicy + DefaultTransitionPolicy
└── core/
    └── config.py                        # Application settings

tests/
├── test_meetings.py                     # Meeting ingestion/retrieval tests
├── test_extraction.py                   # Extraction pipeline tests
├── test_entities.py                     # Entity registry + mention resolution tests
├── test_candidates.py                   # Candidate generation tests
├── test_scoring.py                      # Candidate scoring tests
├── test_resolution.py                   # Resolution Decision Engine tests
├── test_correlation.py                  # Cross-Meeting Correlation tests
├── test_temporal.py                     # Temporal State Engine tests (106 tests)
├── test_memory.py                       # Organisational Memory tests (14 tests)
├── test_insights.py                     # Insight & Change Detection Engine tests (40 tests)
└── test_attention.py                    # Prioritization & Attention Engine tests (26+ tests)
```

---

## Architecture Notes

- **Separation of concerns:** `API → Service → Repository → Storage`. No layer leaks into another.
- **Provider abstraction:** `AbstractExtractionProvider` defines the LLM interface. Swap to Gemini, Anthropic, or a local model by implementing one class and updating one env var.
- **Generator abstraction:** `AbstractCandidateGenerator` defines the candidate generation interface. Swap `LexicalCandidateGenerator` for an embedding-based or contextual generator without changing the service layer.
- **Scorer abstraction:** `AbstractCandidateScorer` defines the scoring interface. Swap for any scoring implementation without touching services or the API.
- **Policy abstraction:** `AbstractResolutionPolicy` defines the decision interface. Swap `ThresholdResolutionPolicy` for an ML-based or human-in-the-loop policy without restructuring the resolution service.
- **Interpreter abstraction:** `AbstractStateInterpreter` defines the state interpretation interface. Swap `KeywordStateInterpreter` for an ML-based or LLM-based interpreter without touching the temporal service or API.
- **Transition policy abstraction:** `AbstractTemporalStatePolicy` defines the transition policy interface. Swap `DefaultTransitionPolicy` for a domain-specific policy (e.g., allowing RESOLVED→IN_PROGRESS for certain entity types) without touching the temporal service.
- **Testability:** `FakeExtractionProvider` makes all extraction tests deterministic with no LLM calls. Entity and candidate tests use isolated in-memory repositories via FastAPI dependency overrides.
- **Repository abstraction:** All repositories (`AbstractMeetingRepository`, `AbstractExtractionRepository`, `AbstractEntityRepository`, `AbstractMentionRepository`) define storage contracts. Swap in PostgreSQL by implementing the same interfaces — service layers do not change.
- **Domain model vs API schema:** Internal models (`app/models/`) evolve freely; public schemas (`app/schemas/`) remain the stable API contract.
- **Error hierarchy:** `ExtractionProviderNotConfiguredError` → 503, `ExtractionProviderResponseError` → 502, `ExtractionError` → 503, `EntityNotFoundError` → 404, `MentionNotFoundError` → 404. All mapped explicitly in their respective routers.
- **Normalisation contract:** The `_normalize()` function (strip, collapse spaces, lowercase) is defined once in `entity_repository.py` and imported by `entity_service.py` and `lexical_candidate_generator.py`. All three layers agree on what constitutes an exact or lexical match.
- **Unresolved mentions are first-class:** `entity_id: null` on a mention is a meaningful data state, not an error. The system never auto-creates an entity for an unresolved mention.
- **Candidate generation is read-only:** `CandidateService.get_candidates()` never modifies `resolution_status` or `entity_id`. Candidates are suggestions, not decisions.
- **Scoring is read-only:** `CandidateScoringService.get_scored_candidates()` never modifies any mention or entity. Scores are evidence, not actions.
- **Resolution Decision is the only mutating stage:** Only `ResolutionService.resolve()` may update a mention's `entity_id` and `resolution_status`. No other stage does this.
- **Correlation is strictly read-only:** `CorrelationService.get_entity_correlations()` never modifies any entity, mention, or resolution state. It aggregates existing resolved data.
- **Temporal State Engine is strictly read-only:** `TemporalStateService.get_entity_timeline()` never modifies any entity, mention, or resolution state. It interprets resolved observations into a lifecycle timeline.
- **Organisational Memory is strictly read-only:** `OrganisationalMemoryService.get_entity_memory()` never modifies any entity, mention, or resolution state. It synthesizes history into structured facts.
- **Insight Engine is strictly read-only:** `InsightService.get_entity_insights()` never modifies any entity, mention, or resolution state. It computes deterministic insights dynamically.
- **Computed on-read:** No new persistent table or repository is needed. State, memory facts, and insights are derived deterministically from existing resolved mentions on every API call.
- **Correlation safety:** Only RESOLVED mentions (entity_id != None AND resolution_status == RESOLVED) participate in correlation, temporal timelines, and organisational memory. AMBIGUOUS and UNRESOLVED mentions are explicitly excluded.
- **Temporal transition safety:** Invalid transitions (e.g., RESOLVED → IN_PROGRESS) are recorded in the timeline with `is_valid_transition=false` but never applied. The current state only advances through valid transitions.
- **Shared repository singletons:** The meetings and entities routers share the same `MeetingRepository` instance via a `get_meeting_repository()` accessor exported from `meetings.py`. This ensures correlation and temporal queries see all ingested meetings.
- **Score ≠ probability:** Lexical similarity scores are outputs of the scoring function. They are explicitly documented as non-probabilistic throughout the codebase.
- **Safe abstention:** The system prefers AMBIGUOUS/UNRESOLVED over incorrect RESOLVED. Incorrect entity assignments are harder to fix than unresolved mentions.
- **Storage today:** Simple in-memory dictionaries. Suitable for development and testing only.
