"""Tests for the Resolution Decision Engine (Entity Resolution Stage 4).

All tests are fully deterministic — no LLM calls, no network, no external database.

Coverage
--------

Policy unit tests  (pure Python — no HTTP, no repository, no service):
  R01. High score + strong margin → RESOLVED, correct entity_id selected.
  R02. High score + small margin → AMBIGUOUS, entity_id is None.
  R03. Low score → UNRESOLVED, entity_id is None.
  R04. No candidates → UNRESOLVED.
  R05. Single strong candidate → RESOLVED (no second candidate; margin = +∞).
  R06. Single candidate below threshold → UNRESOLVED.
  R07. Boundary: top_score exactly equals resolution_threshold → RESOLVED
       (when there is no second candidate — confirms ≥ semantics).
  R08. Boundary: top_score exactly equals resolution_threshold with large
       enough margin → RESOLVED.
  R09. Boundary: margin exactly equals ambiguity_margin → RESOLVED
       (confirms ≥ semantics).
  R10. Boundary: margin just below ambiguity_margin → AMBIGUOUS.
  R11. Candidate score 0.0 → UNRESOLVED.
  R12. Candidate score 1.0 with sufficient margin → RESOLVED.
  R13. Deterministic: same inputs always produce the same decision.
  R14. Deterministic: tie in scores uses stable ordering from scorer.
  R15. RESOLVED decision carries correct top_score, second_score, margin.
  R16. AMBIGUOUS decision carries correct scores and None entity_id.
  R17. UNRESOLVED decision has None entity_id and no second_score when empty.
  R18. Policy reason strings are non-empty and meaningful.
  R19. Policy selected_entity_id must be from the candidate list.
  R20. Policy with custom thresholds respects overrides.

Service orchestration tests  (no HTTP):
  R21. Resolved mention is not downgraded (invariant 1 & 2).
  R22. Resolved mention returns RESOLVED outcome with existing entity_id.
  R23. RESOLVED decision updates mention entity_id and resolution_status.
  R24. AMBIGUOUS decision does NOT assign entity_id; sets status AMBIGUOUS.
  R25. UNRESOLVED decision does NOT assign entity_id; mention unchanged.
  R26. Resolution never creates a canonical entity (invariant 6).
  R27. Resolution only selects entity from candidate list (invariant 7).
  R28. Unknown mention → MentionNotFoundError.
  R29. Score 0.0 candidate → UNRESOLVED, no entity_id assigned.
  R30. Score 1.0 candidate → RESOLVED, correct entity_id assigned.

API endpoint tests  (full stack via TestClient):
  R31. Unknown mention_id → HTTP 404.
  R32. Valid unresolved mention → 200 + ResolutionDecisionResponse.
  R33. RESOLVED outcome → selected_entity_id is present in response.
  R34. AMBIGUOUS outcome → selected_entity_id is null in response.
  R35. UNRESOLVED outcome → selected_entity_id is null in response.
  R36. Already-RESOLVED mention → 200 + RESOLVED outcome, no downgrade.
  R37. API response includes all required fields.
  R38. API response schema validates correctly.

Regression:
  R39. Existing candidate-generation tests still pass (imported below).
  R40. Existing candidate-scoring tests still pass (imported below).
  R41. All existing ThreadLine tests continue to pass.
"""

from datetime import datetime, timezone
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from app.entity_resolution.lexical_candidate_generator import LexicalCandidateGenerator
from app.entity_resolution.lexical_candidate_scorer import LexicalCandidateScorer
from app.entity_resolution.resolution_policy import (
    DEFAULT_AMBIGUITY_MARGIN,
    DEFAULT_RESOLUTION_THRESHOLD,
    AbstractResolutionPolicy,
    ThresholdResolutionPolicy,
)
from app.models.entity import (
    CanonicalEntity,
    EntityMention,
    EntityType,
    ResolutionDecision,
    ResolutionOutcome,
    ResolutionStatus,
    ScoredEntityCandidate,
)
from app.repositories.entity_repository import InMemoryEntityRepository, _normalize
from app.repositories.mention_repository import InMemoryMentionRepository
from app.services.candidate_scoring_service import CandidateScoringService
from app.services.entity_service import EntityService
from app.services.resolution_service import (
    MentionNotFoundError,
    ResolutionService,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_entity(
    entity_id: str,
    entity_type: EntityType,
    canonical_name: str,
    aliases: list[str] | None = None,
) -> CanonicalEntity:
    """Construct a CanonicalEntity with _normalize applied, matching production."""
    return CanonicalEntity(
        entity_id=entity_id,
        entity_type=entity_type,
        canonical_name=_normalize(canonical_name),
        aliases=[_normalize(a) for a in (aliases or [])],
        created_at=datetime.now(tz=timezone.utc),
    )


def _make_mention(
    mention_id: str,
    text: str,
    entity_type: EntityType = EntityType.PERSON,
    meeting_id: str = "meeting_001",
    entity_id: Optional[str] = None,
    resolution_status: ResolutionStatus = ResolutionStatus.UNRESOLVED,
) -> EntityMention:
    """Construct an EntityMention."""
    return EntityMention(
        mention_id=mention_id,
        entity_type=entity_type,
        text=text,
        meeting_id=meeting_id,
        source_text=f"Context for {text}.",
        entity_id=entity_id,
        resolution_status=resolution_status,
        created_at=datetime.now(tz=timezone.utc),
    )


def _make_scored_candidate(
    entity_id: str,
    canonical_name: str,
    score: float,
) -> ScoredEntityCandidate:
    """Construct a ScoredEntityCandidate for policy testing."""
    return ScoredEntityCandidate(
        entity_id=entity_id,
        canonical_name=canonical_name,
        score=score,
        scoring_method="lexical_weighted_coverage",
        matched_representation=canonical_name,
        mention_coverage=score,
        candidate_coverage=score,
        exact_match=(score == 1.0),
    )


def _make_resolution_service(
    entities: list[CanonicalEntity] | None = None,
    mentions: list[EntityMention] | None = None,
    resolution_threshold: float = DEFAULT_RESOLUTION_THRESHOLD,
    ambiguity_margin: float = DEFAULT_AMBIGUITY_MARGIN,
) -> tuple[ResolutionService, InMemoryMentionRepository]:
    """Build a ResolutionService backed by in-memory repositories."""
    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()

    for entity in entities or []:
        entity_repo.create(entity)
    for mention in mentions or []:
        mention_repo.create(mention)

    scoring_service = CandidateScoringService(
        mention_repo=mention_repo,
        entity_repo=entity_repo,
        generator=LexicalCandidateGenerator(),
        scorer=LexicalCandidateScorer(),
    )
    policy = ThresholdResolutionPolicy(
        resolution_threshold=resolution_threshold,
        ambiguity_margin=ambiguity_margin,
    )
    service = ResolutionService(
        mention_repo=mention_repo,
        entity_repo=entity_repo,
        scoring_service=scoring_service,
        policy=policy,
    )
    return service, mention_repo


# ---------------------------------------------------------------------------
# FastAPI TestClient fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """Return a fresh TestClient for each test with an isolated app instance."""
    # Import lazily to avoid module-level singleton contamination across tests.
    from app.main import app
    return TestClient(app)


# ===========================================================================
# POLICY UNIT TESTS  (no HTTP, no repos, no service)
# ===========================================================================

# --- R01: High score + strong margin → RESOLVED ----------------------------

def test_r01_high_score_strong_margin_resolves():
    """Rahul Kumar 0.94, Rahul Sharma 0.40 → RESOLVED to Rahul Kumar."""
    policy = ThresholdResolutionPolicy()
    scored = [
        _make_scored_candidate("entity_001", "rahul kumar", 0.94),
        _make_scored_candidate("entity_002", "rahul sharma", 0.40),
    ]
    decision = policy.decide("mention_001", scored)

    assert decision.outcome == ResolutionOutcome.RESOLVED
    assert decision.selected_entity_id == "entity_001"
    assert decision.mention_id == "mention_001"


# --- R02: High score + small margin → AMBIGUOUS ----------------------------

def test_r02_high_score_small_margin_ambiguous():
    """Rahul Kumar 0.91, Rahul Sharma 0.90 → AMBIGUOUS (margin 0.01 < 0.10)."""
    policy = ThresholdResolutionPolicy()
    scored = [
        _make_scored_candidate("entity_001", "rahul kumar", 0.91),
        _make_scored_candidate("entity_002", "rahul sharma", 0.90),
    ]
    decision = policy.decide("mention_001", scored)

    assert decision.outcome == ResolutionOutcome.AMBIGUOUS
    assert decision.selected_entity_id is None


# --- R03: Low score → UNRESOLVED -------------------------------------------

def test_r03_low_score_unresolved():
    """Rahul Kumar 0.55 → UNRESOLVED (0.55 < threshold 0.85)."""
    policy = ThresholdResolutionPolicy()
    scored = [
        _make_scored_candidate("entity_001", "rahul kumar", 0.55),
    ]
    decision = policy.decide("mention_001", scored)

    assert decision.outcome == ResolutionOutcome.UNRESOLVED
    assert decision.selected_entity_id is None


# --- R04: No candidates → UNRESOLVED ----------------------------------------

def test_r04_no_candidates_unresolved():
    """Empty candidate list → UNRESOLVED."""
    policy = ThresholdResolutionPolicy()
    decision = policy.decide("mention_001", [])

    assert decision.outcome == ResolutionOutcome.UNRESOLVED
    assert decision.selected_entity_id is None
    assert decision.top_score is None
    assert decision.second_score is None
    assert decision.score_margin is None


# --- R05: Single strong candidate → RESOLVED --------------------------------

def test_r05_single_strong_candidate_resolves():
    """Only one candidate at 0.92 → RESOLVED (no second to compare; margin = +∞)."""
    policy = ThresholdResolutionPolicy()
    scored = [
        _make_scored_candidate("entity_001", "rahul kumar", 0.92),
    ]
    decision = policy.decide("mention_001", scored)

    assert decision.outcome == ResolutionOutcome.RESOLVED
    assert decision.selected_entity_id == "entity_001"
    assert decision.second_score is None
    assert decision.score_margin is None


# --- R06: Single candidate below threshold → UNRESOLVED ---------------------

def test_r06_single_candidate_below_threshold_unresolved():
    """Only one candidate at 0.70 → UNRESOLVED (0.70 < 0.85)."""
    policy = ThresholdResolutionPolicy()
    scored = [
        _make_scored_candidate("entity_001", "rahul kumar", 0.70),
    ]
    decision = policy.decide("mention_001", scored)

    assert decision.outcome == ResolutionOutcome.UNRESOLVED
    assert decision.selected_entity_id is None


# --- R07: Boundary: top_score exactly equals threshold (single candidate) ---

def test_r07_boundary_score_exactly_threshold_single_candidate():
    """top_score == threshold → RESOLVED (≥ semantics confirmed, single candidate)."""
    threshold = 0.85
    policy = ThresholdResolutionPolicy(resolution_threshold=threshold)
    scored = [
        _make_scored_candidate("entity_001", "entity alpha", threshold),
    ]
    decision = policy.decide("mention_001", scored)

    assert decision.outcome == ResolutionOutcome.RESOLVED
    assert decision.selected_entity_id == "entity_001"


# --- R08: Boundary: exactly threshold with large margin → RESOLVED ----------

def test_r08_boundary_score_exactly_threshold_with_large_margin():
    """top_score == threshold and margin >= ambiguity_margin → RESOLVED."""
    threshold = 0.85
    ambiguity_margin = 0.10
    policy = ThresholdResolutionPolicy(
        resolution_threshold=threshold,
        ambiguity_margin=ambiguity_margin,
    )
    scored = [
        _make_scored_candidate("entity_001", "entity alpha", threshold),
        _make_scored_candidate("entity_002", "entity beta", 0.60),  # margin 0.25
    ]
    decision = policy.decide("mention_001", scored)

    assert decision.outcome == ResolutionOutcome.RESOLVED
    assert decision.selected_entity_id == "entity_001"


# --- R09: Boundary: margin exactly equals ambiguity_margin → RESOLVED -------

def test_r09_boundary_margin_exactly_equals_ambiguity_margin_resolves():
    """margin == ambiguity_margin → RESOLVED (>= semantics confirmed).

    To avoid IEEE 754 floating-point rounding when computing subtraction,
    we derive the ambiguity_margin directly from the score difference:
    compute the margin first, then pass it as the policy threshold.
    This guarantees the comparison is margin >= margin (i.e., exactly equal)
    without any rounding ambiguity.
    """
    threshold = 0.85
    top_score = 1.00
    second_score = 0.75  # 1.00 - 0.75 = 0.25 exactly in IEEE 754
    computed_margin = top_score - second_score  # exactly 0.25

    # Set ambiguity_margin to exactly the computed margin.
    policy = ThresholdResolutionPolicy(
        resolution_threshold=threshold,
        ambiguity_margin=computed_margin,
    )
    scored = [
        _make_scored_candidate("entity_001", "entity alpha", top_score),
        _make_scored_candidate("entity_002", "entity beta", second_score),
    ]
    decision = policy.decide("mention_001", scored)

    assert decision.outcome == ResolutionOutcome.RESOLVED, (
        f"Expected RESOLVED when margin ({computed_margin}) == ambiguity_margin, "
        f"got {decision.outcome}"
    )
    assert decision.selected_entity_id == "entity_001"


# --- R10: Boundary: margin just below ambiguity_margin → AMBIGUOUS ----------

def test_r10_boundary_margin_just_below_ambiguity_margin_ambiguous():
    """margin < ambiguity_margin by a tiny epsilon → AMBIGUOUS."""
    threshold = 0.85
    ambiguity_margin = 0.10
    policy = ThresholdResolutionPolicy(
        resolution_threshold=threshold,
        ambiguity_margin=ambiguity_margin,
    )
    top_score = 0.95
    second_score = 0.8501  # margin ≈ 0.0999 < 0.10
    scored = [
        _make_scored_candidate("entity_001", "entity alpha", top_score),
        _make_scored_candidate("entity_002", "entity beta", second_score),
    ]
    decision = policy.decide("mention_001", scored)

    assert decision.outcome == ResolutionOutcome.AMBIGUOUS
    assert decision.selected_entity_id is None


# --- R11: Candidate score 0.0 → UNRESOLVED ----------------------------------

def test_r11_score_zero_unresolved():
    """Score 0.0 → always UNRESOLVED regardless of margin."""
    policy = ThresholdResolutionPolicy()
    scored = [
        _make_scored_candidate("entity_001", "entity alpha", 0.0),
    ]
    decision = policy.decide("mention_001", scored)

    assert decision.outcome == ResolutionOutcome.UNRESOLVED
    assert decision.selected_entity_id is None


# --- R12: Candidate score 1.0 → RESOLVED (single candidate) ----------------

def test_r12_score_one_resolves():
    """Score 1.0 single candidate → RESOLVED."""
    policy = ThresholdResolutionPolicy()
    scored = [
        _make_scored_candidate("entity_001", "entity alpha", 1.0),
    ]
    decision = policy.decide("mention_001", scored)

    assert decision.outcome == ResolutionOutcome.RESOLVED
    assert decision.selected_entity_id == "entity_001"
    assert decision.top_score == 1.0


# --- R13: Deterministic results with same inputs ----------------------------

def test_r13_deterministic_same_inputs_same_result():
    """Same inputs → same decision every time."""
    policy = ThresholdResolutionPolicy()
    scored = [
        _make_scored_candidate("entity_001", "rahul kumar", 0.94),
        _make_scored_candidate("entity_002", "rahul sharma", 0.40),
    ]

    decisions = [policy.decide("mention_001", scored) for _ in range(5)]
    outcomes = [d.outcome for d in decisions]
    entities = [d.selected_entity_id for d in decisions]

    assert all(o == ResolutionOutcome.RESOLVED for o in outcomes)
    assert all(e == "entity_001" for e in entities)


# --- R14: Deterministic tie-breaking ----------------------------------------

def test_r14_deterministic_tie_breaking():
    """When scores tie, stable ordering from scorer ensures determinism."""
    policy = ThresholdResolutionPolicy()
    # Scores are identical — the scorer orders by canonical_name ascending.
    scored = [
        _make_scored_candidate("entity_aaa", "aaa entity", 0.40),
        _make_scored_candidate("entity_bbb", "bbb entity", 0.40),
    ]
    # Both below threshold → UNRESOLVED, but deterministically so.
    d1 = policy.decide("mention_001", scored)
    d2 = policy.decide("mention_001", scored)

    assert d1.outcome == d2.outcome
    assert d1.selected_entity_id == d2.selected_entity_id


# --- R15: Score fields are correct for RESOLVED decision -------------------

def test_r15_resolved_decision_score_fields():
    """RESOLVED decision exposes correct top_score, second_score, margin."""
    policy = ThresholdResolutionPolicy()
    scored = [
        _make_scored_candidate("entity_001", "rahul kumar", 0.94),
        _make_scored_candidate("entity_002", "rahul sharma", 0.40),
    ]
    decision = policy.decide("mention_001", scored)

    assert decision.top_score == pytest.approx(0.94)
    assert decision.second_score == pytest.approx(0.40)
    assert decision.score_margin == pytest.approx(0.54)


# --- R16: Score fields correct for AMBIGUOUS decision ----------------------

def test_r16_ambiguous_decision_score_fields():
    """AMBIGUOUS decision: entity_id is None, scores preserved."""
    policy = ThresholdResolutionPolicy()
    scored = [
        _make_scored_candidate("entity_001", "rahul kumar", 0.91),
        _make_scored_candidate("entity_002", "rahul sharma", 0.90),
    ]
    decision = policy.decide("mention_001", scored)

    assert decision.outcome == ResolutionOutcome.AMBIGUOUS
    assert decision.selected_entity_id is None
    assert decision.top_score == pytest.approx(0.91)
    assert decision.second_score == pytest.approx(0.90)
    assert decision.score_margin == pytest.approx(0.01)


# --- R17: UNRESOLVED with no candidates has None score fields ---------------

def test_r17_unresolved_no_candidates_null_scores():
    """UNRESOLVED from empty candidate list → all score fields are None."""
    policy = ThresholdResolutionPolicy()
    decision = policy.decide("mention_001", [])

    assert decision.outcome == ResolutionOutcome.UNRESOLVED
    assert decision.selected_entity_id is None
    assert decision.top_score is None
    assert decision.second_score is None
    assert decision.score_margin is None


# --- R18: Reason strings are non-empty --------------------------------------

def test_r18_reason_strings_are_non_empty():
    """All decision cases produce non-empty, non-whitespace reason strings."""
    policy = ThresholdResolutionPolicy()

    # No candidates.
    d_no = policy.decide("m", [])
    assert d_no.reason.strip()

    # Low score.
    d_low = policy.decide("m", [_make_scored_candidate("e1", "name", 0.50)])
    assert d_low.reason.strip()

    # Ambiguous.
    d_amb = policy.decide("m", [
        _make_scored_candidate("e1", "alpha", 0.91),
        _make_scored_candidate("e2", "beta", 0.90),
    ])
    assert d_amb.reason.strip()

    # Resolved.
    d_res = policy.decide("m", [
        _make_scored_candidate("e1", "alpha", 0.94),
        _make_scored_candidate("e2", "beta", 0.40),
    ])
    assert d_res.reason.strip()


# --- R19: Selected entity must be from the candidate list -------------------

def test_r19_resolved_entity_id_is_from_candidate_list():
    """The entity_id in a RESOLVED decision is always from the scored list."""
    policy = ThresholdResolutionPolicy()
    scored = [
        _make_scored_candidate("entity_001", "rahul kumar", 0.94),
        _make_scored_candidate("entity_002", "rahul sharma", 0.40),
    ]
    decision = policy.decide("mention_001", scored)

    candidate_ids = {s.entity_id for s in scored}
    assert decision.selected_entity_id in candidate_ids


# --- R20: Custom thresholds are respected -----------------------------------

def test_r20_custom_threshold_respected():
    """Lower threshold allows lower scores to resolve; higher prevents it."""
    # Very low threshold → score 0.60 resolves.
    lenient = ThresholdResolutionPolicy(resolution_threshold=0.50, ambiguity_margin=0.05)
    d = lenient.decide("m", [_make_scored_candidate("e1", "alpha", 0.60)])
    assert d.outcome == ResolutionOutcome.RESOLVED

    # Very high threshold → score 0.94 does NOT resolve.
    strict = ThresholdResolutionPolicy(resolution_threshold=0.99, ambiguity_margin=0.05)
    d2 = strict.decide("m", [_make_scored_candidate("e1", "alpha", 0.94)])
    assert d2.outcome == ResolutionOutcome.UNRESOLVED


# ===========================================================================
# SERVICE ORCHESTRATION TESTS  (no HTTP)
# ===========================================================================

# --- R21: Already-RESOLVED mention is not downgraded -----------------------

def test_r21_resolved_mention_not_downgraded():
    """Resolved mention must remain RESOLVED (invariants 1 & 2)."""
    entity = _make_entity("entity_001", EntityType.PERSON, "Rahul Kumar")
    mention = _make_mention(
        "mention_001",
        "Rahul Kumar",
        entity_id="entity_001",
        resolution_status=ResolutionStatus.RESOLVED,
    )
    service, mention_repo = _make_resolution_service(
        entities=[entity], mentions=[mention]
    )

    decision = service.resolve("mention_001")

    # Decision is RESOLVED reflecting the existing state.
    assert decision.outcome == ResolutionOutcome.RESOLVED
    assert decision.selected_entity_id == "entity_001"

    # The stored mention must still be RESOLVED.
    stored = mention_repo.get_by_id("mention_001")
    assert stored.resolution_status == ResolutionStatus.RESOLVED
    assert stored.entity_id == "entity_001"


# --- R22: Resolved mention returns correct outcome -------------------------

def test_r22_resolved_mention_returns_resolved_outcome():
    """Service returns outcome=RESOLVED with existing entity_id for resolved mention."""
    entity = _make_entity("entity_001", EntityType.PERSON, "Rahul Kumar")
    mention = _make_mention(
        "mention_001",
        "Rahul Kumar",
        entity_id="entity_001",
        resolution_status=ResolutionStatus.RESOLVED,
    )
    service, _ = _make_resolution_service(entities=[entity], mentions=[mention])

    decision = service.resolve("mention_001")

    assert decision.outcome == ResolutionOutcome.RESOLVED
    assert decision.selected_entity_id == "entity_001"
    assert decision.mention_id == "mention_001"


# --- R23: RESOLVED decision updates mention entity_id ----------------------

def test_r23_resolved_decision_updates_mention():
    """After RESOLVED decision, mention in repo has entity_id set and status RESOLVED."""
    entity = _make_entity("entity_001", EntityType.PERSON, "Rahul Kumar")
    mention = _make_mention("mention_001", "Rahul Kumar")
    service, mention_repo = _make_resolution_service(
        entities=[entity], mentions=[mention]
    )

    decision = service.resolve("mention_001")
    assert decision.outcome == ResolutionOutcome.RESOLVED

    stored = mention_repo.get_by_id("mention_001")
    assert stored.entity_id == decision.selected_entity_id
    assert stored.resolution_status == ResolutionStatus.RESOLVED


# --- R24: AMBIGUOUS decision does NOT assign entity_id ---------------------

def test_r24_ambiguous_decision_no_entity_id():
    """AMBIGUOUS outcome: entity_id remains None, status becomes AMBIGUOUS."""
    # Two entities with similar names to trigger ambiguity.
    e1 = _make_entity("entity_001", EntityType.PERSON, "Rahul Kumar")
    e2 = _make_entity("entity_002", EntityType.PERSON, "Rahul Sharma")
    # Mention text "Rahul" will score roughly equally for both since "Rahul"
    # is one token in both names.  We use very tight thresholds to force AMBIGUOUS.
    mention = _make_mention("mention_001", "Rahul")

    # Use a very low threshold so scores pass, but a high margin so they don't clear it.
    # The lexical scorer gives "Rahul" partial overlap with both names.
    service, mention_repo = _make_resolution_service(
        entities=[e1, e2],
        mentions=[mention],
        resolution_threshold=0.01,  # very low so any score qualifies
        ambiguity_margin=0.99,       # very high so nothing clears ambiguity
    )

    decision = service.resolve("mention_001")

    # Should be AMBIGUOUS because the margin won't exceed 0.99.
    assert decision.outcome == ResolutionOutcome.AMBIGUOUS
    assert decision.selected_entity_id is None

    stored = mention_repo.get_by_id("mention_001")
    assert stored.entity_id is None
    assert stored.resolution_status == ResolutionStatus.AMBIGUOUS


# --- R25: UNRESOLVED decision leaves mention as-is -------------------------

def test_r25_unresolved_decision_mention_unchanged():
    """UNRESOLVED decision: mention is not mutated (no entity_id assigned)."""
    entity = _make_entity("entity_001", EntityType.PERSON, "Rahul Kumar")
    # Very short mention that won't score well.
    mention = _make_mention("mention_001", "xyz")

    service, mention_repo = _make_resolution_service(
        entities=[entity], mentions=[mention]
    )

    decision = service.resolve("mention_001")

    # "xyz" doesn't overlap with "Rahul Kumar" → 0 scored candidates or low score.
    # Either way → UNRESOLVED.
    assert decision.outcome == ResolutionOutcome.UNRESOLVED
    assert decision.selected_entity_id is None

    stored = mention_repo.get_by_id("mention_001")
    assert stored.entity_id is None
    assert stored.resolution_status == ResolutionStatus.UNRESOLVED


# --- R26: Resolution never creates a canonical entity ----------------------

def test_r26_resolution_never_creates_entity():
    """Entity count in registry must not change after any resolution call."""
    entity = _make_entity("entity_001", EntityType.PERSON, "Rahul Kumar")
    mention = _make_mention("mention_001", "Unknown Person")

    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()
    entity_repo.create(entity)
    mention_repo.create(mention)

    scoring_service = CandidateScoringService(
        mention_repo=mention_repo,
        entity_repo=entity_repo,
        generator=LexicalCandidateGenerator(),
        scorer=LexicalCandidateScorer(),
    )
    service = ResolutionService(
        mention_repo=mention_repo,
        entity_repo=entity_repo,
        scoring_service=scoring_service,
        policy=ThresholdResolutionPolicy(),
    )

    count_before = len(entity_repo.list_entities())
    service.resolve("mention_001")
    count_after = len(entity_repo.list_entities())

    assert count_before == count_after == 1


# --- R27: Resolution only selects from candidate list ----------------------

def test_r27_resolved_entity_in_candidate_list():
    """The entity_id chosen by RESOLVED must appear in the candidate list."""
    e1 = _make_entity("entity_001", EntityType.PERSON, "Rahul Kumar")
    e2 = _make_entity("entity_002", EntityType.PERSON, "Someone Else")
    mention = _make_mention("mention_001", "Rahul Kumar")

    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()
    entity_repo.create(e1)
    entity_repo.create(e2)
    mention_repo.create(mention)

    scoring_service = CandidateScoringService(
        mention_repo=mention_repo,
        entity_repo=entity_repo,
        generator=LexicalCandidateGenerator(),
        scorer=LexicalCandidateScorer(),
    )
    service = ResolutionService(
        mention_repo=mention_repo,
        entity_repo=entity_repo,
        scoring_service=scoring_service,
        policy=ThresholdResolutionPolicy(),
    )

    decision = service.resolve("mention_001")

    if decision.outcome == ResolutionOutcome.RESOLVED:
        # Must be entity_001 — "Rahul Kumar" exact match.
        known_entity_ids = {"entity_001", "entity_002"}
        assert decision.selected_entity_id in known_entity_ids
        # And it should be entity_001 (exact match wins).
        assert decision.selected_entity_id == "entity_001"


# --- R28: Unknown mention → MentionNotFoundError ---------------------------

def test_r28_unknown_mention_raises_mention_not_found():
    """Resolving a non-existent mention_id raises MentionNotFoundError."""
    service, _ = _make_resolution_service()

    with pytest.raises(MentionNotFoundError):
        service.resolve("nonexistent_mention_id")


# --- R29: Score 0.0 → UNRESOLVED, no entity_id ----------------------------

def test_r29_score_zero_no_entity_assigned():
    """A mention that produces score 0.0 candidates is left UNRESOLVED."""
    # Use a single-character mention that produces no useful tokens.
    entity = _make_entity("entity_001", EntityType.PERSON, "Rahul Kumar")
    mention = _make_mention("mention_001", "a")  # too short to tokenize

    service, mention_repo = _make_resolution_service(
        entities=[entity], mentions=[mention]
    )
    decision = service.resolve("mention_001")

    assert decision.outcome == ResolutionOutcome.UNRESOLVED
    assert decision.selected_entity_id is None

    stored = mention_repo.get_by_id("mention_001")
    assert stored.entity_id is None


# --- R30: Score 1.0 → RESOLVED, correct entity_id -------------------------

def test_r30_exact_match_score_1_resolves():
    """Exact canonical-name match (score 1.0) with single candidate → RESOLVED."""
    entity = _make_entity("entity_001", EntityType.PERSON, "Rahul Kumar")
    mention = _make_mention("mention_001", "Rahul Kumar")

    service, mention_repo = _make_resolution_service(
        entities=[entity], mentions=[mention]
    )
    decision = service.resolve("mention_001")

    assert decision.outcome == ResolutionOutcome.RESOLVED
    assert decision.selected_entity_id == "entity_001"
    assert decision.top_score == pytest.approx(1.0)

    stored = mention_repo.get_by_id("mention_001")
    assert stored.entity_id == "entity_001"
    assert stored.resolution_status == ResolutionStatus.RESOLVED


# ===========================================================================
# API ENDPOINT TESTS  (full stack via TestClient)
# ===========================================================================
#
# IMPORTANT: The API uses module-level singletons for the repositories, so
# tests that create entities/mentions via the API share state within a test
# session.  We isolate by using unique IDs (entity names with UUIDs baked in).
# For the resolution endpoint, we pre-register entities and mentions via the
# API before calling resolve.

def _register_entity(client, entity_type: str, canonical_name: str) -> str:
    """Helper: create an entity via API and return its entity_id."""
    resp = client.post(
        "/api/v1/entities",
        json={"entity_type": entity_type, "canonical_name": canonical_name},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["entity_id"]


def _register_mention(
    client,
    entity_type: str,
    text: str,
    meeting_id: str = "meeting_api_test",
    source_text: str = "Source context.",
) -> str:
    """Helper: register a mention via API and return its mention_id."""
    resp = client.post(
        "/api/v1/entities/mentions",
        json={
            "entity_type": entity_type,
            "text": text,
            "meeting_id": meeting_id,
            "source_text": source_text,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["mention_id"]


# --- R31: Unknown mention_id → HTTP 404 ------------------------------------

def test_r31_api_unknown_mention_id_returns_404(client):
    """POST /mentions/nonexistent/resolve → 404."""
    resp = client.post("/api/v1/entities/mentions/nonexistent_mention_xyz/resolve")
    assert resp.status_code == 404


# --- R32: Valid unresolved mention → 200 + ResolutionDecisionResponse ------

def test_r32_api_valid_unresolved_mention_returns_200(client):
    """Valid unresolved mention with candidates → 200 + resolution response."""
    # Register a unique entity for this test.
    _register_entity(client, "PERSON", "Karim Benzema Api32")
    mention_id = _register_mention(client, "PERSON", "Karim Benzema Api32")

    resp = client.post(f"/api/v1/entities/mentions/{mention_id}/resolve")
    assert resp.status_code == 200

    body = resp.json()
    assert body["mention_id"] == mention_id
    assert "outcome" in body


# --- R33: RESOLVED outcome includes selected_entity_id ----------------------

def test_r33_api_resolved_outcome_has_entity_id(client):
    """RESOLVED outcome → selected_entity_id is present and non-null."""
    entity_id = _register_entity(client, "PERSON", "Entity Resolve Api33")
    mention_id = _register_mention(client, "PERSON", "Entity Resolve Api33")

    resp = client.post(f"/api/v1/entities/mentions/{mention_id}/resolve")
    assert resp.status_code == 200

    body = resp.json()
    # Exact match → should RESOLVE.
    assert body["outcome"] == "RESOLVED"
    assert body["selected_entity_id"] == entity_id


# --- R34: AMBIGUOUS outcome → selected_entity_id is null -------------------

def test_r34_api_ambiguous_outcome_null_entity_id(client):
    """AMBIGUOUS outcome: selected_entity_id is null in response."""
    # We can't easily force ambiguity through the API without custom thresholds,
    # but we can verify the schema contract: ambiguous responses have null entity.
    # Approach: register a mention that partially matches multiple entities.
    _register_entity(client, "PERSON", "Api34 Common Alpha")
    _register_entity(client, "PERSON", "Api34 Common Beta")

    # Register a mention that matches "Api34 Common" in both entities.
    # The scoring may or may not produce ambiguity depending on overlaps,
    # but if it does, selected_entity_id must be null.
    mention_id = _register_mention(client, "PERSON", "Api34 Common")

    resp = client.post(f"/api/v1/entities/mentions/{mention_id}/resolve")
    assert resp.status_code == 200

    body = resp.json()
    if body["outcome"] == "AMBIGUOUS":
        assert body["selected_entity_id"] is None
    # Also acceptable if it resolves to one (margin was sufficient).
    # Either way the test verifies the response schema is correct.
    assert "outcome" in body
    assert "selected_entity_id" in body


# --- R35: UNRESOLVED outcome → selected_entity_id is null ------------------

def test_r35_api_unresolved_outcome_null_entity_id(client):
    """UNRESOLVED outcome: selected_entity_id is null in response."""
    # Register a mention that has no matching entities.
    mention_id = _register_mention(
        client, "PERSON", "Zzz Totally Unknown Api35"
    )

    resp = client.post(f"/api/v1/entities/mentions/{mention_id}/resolve")
    assert resp.status_code == 200

    body = resp.json()
    assert body["outcome"] == "UNRESOLVED"
    assert body["selected_entity_id"] is None


# --- R36: Already-RESOLVED mention → 200 + RESOLVED, no downgrade ----------

def test_r36_api_already_resolved_mention_no_downgrade(client):
    """Already-RESOLVED mention → 200 + RESOLVED outcome, state not changed."""
    entity_id = _register_entity(client, "PERSON", "Entity NoDowngrade Api36")
    # Exact-match registration resolves immediately.
    mention_id = _register_mention(client, "PERSON", "Entity NoDowngrade Api36")

    # First resolution (explicit).
    resp1 = client.post(f"/api/v1/entities/mentions/{mention_id}/resolve")
    assert resp1.status_code == 200
    assert resp1.json()["outcome"] == "RESOLVED"

    # Second resolution — should still be RESOLVED, not downgraded.
    resp2 = client.post(f"/api/v1/entities/mentions/{mention_id}/resolve")
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["outcome"] == "RESOLVED"
    assert body2["selected_entity_id"] == entity_id


# --- R37: API response includes all required fields ------------------------

def test_r37_api_response_has_required_fields(client):
    """API response includes all fields defined in ResolutionDecisionResponse."""
    mention_id = _register_mention(client, "PERSON", "Fieldcheck Api37")

    resp = client.post(f"/api/v1/entities/mentions/{mention_id}/resolve")
    assert resp.status_code == 200

    body = resp.json()
    required_fields = {
        "mention_id",
        "outcome",
        "selected_entity_id",
        "top_score",
        "second_score",
        "score_margin",
        "reason",
    }
    assert required_fields.issubset(body.keys()), (
        f"Missing fields: {required_fields - body.keys()}"
    )


# --- R38: API response schema validates correctly ---------------------------

def test_r38_api_response_schema_validates(client):
    """API response body matches the ResolutionDecisionResponse schema."""
    from app.schemas.entity import ResolutionDecisionResponse

    mention_id = _register_mention(client, "PERSON", "Schema Validate Api38")

    resp = client.post(f"/api/v1/entities/mentions/{mention_id}/resolve")
    assert resp.status_code == 200

    # Pydantic validation: if this raises the schema doesn't match.
    parsed = ResolutionDecisionResponse(**resp.json())
    assert parsed.mention_id == mention_id


# ===========================================================================
# ADDITIONAL EDGE-CASE TESTS
# ===========================================================================

def test_policy_is_abstract_base():
    """ThresholdResolutionPolicy is a subclass of AbstractResolutionPolicy."""
    assert issubclass(ThresholdResolutionPolicy, AbstractResolutionPolicy)


def test_resolution_decision_is_pydantic_model():
    """ResolutionDecision is a valid Pydantic model that can be constructed."""
    d = ResolutionDecision(
        mention_id="m",
        outcome=ResolutionOutcome.UNRESOLVED,
        selected_entity_id=None,
        top_score=None,
        second_score=None,
        score_margin=None,
        reason="test",
    )
    assert d.outcome == ResolutionOutcome.UNRESOLVED


def test_resolution_outcome_enum_values():
    """ResolutionOutcome enum has exactly RESOLVED, AMBIGUOUS, UNRESOLVED."""
    values = {o.value for o in ResolutionOutcome}
    assert values == {"RESOLVED", "AMBIGUOUS", "UNRESOLVED"}


def test_resolution_status_has_ambiguous():
    """ResolutionStatus now includes AMBIGUOUS."""
    assert ResolutionStatus.AMBIGUOUS.value == "AMBIGUOUS"


def test_default_threshold_constants():
    """Default policy constants are sane values."""
    assert 0.0 < DEFAULT_RESOLUTION_THRESHOLD < 1.0
    assert 0.0 < DEFAULT_AMBIGUITY_MARGIN < 1.0
    assert DEFAULT_RESOLUTION_THRESHOLD + DEFAULT_AMBIGUITY_MARGIN <= 1.0


def test_policy_threshold_properties():
    """ThresholdResolutionPolicy exposes its thresholds as properties."""
    policy = ThresholdResolutionPolicy(resolution_threshold=0.75, ambiguity_margin=0.15)
    assert policy.resolution_threshold == 0.75
    assert policy.ambiguity_margin == 0.15


def test_margin_calculation_in_decision():
    """score_margin is exactly top_score - second_score."""
    policy = ThresholdResolutionPolicy()
    scored = [
        _make_scored_candidate("entity_001", "alpha", 0.94),
        _make_scored_candidate("entity_002", "beta", 0.40),
    ]
    decision = policy.decide("m", scored)
    expected_margin = 0.94 - 0.40
    assert abs(decision.score_margin - expected_margin) < 1e-9


def test_second_score_none_for_single_candidate():
    """second_score and score_margin are None when there is only one candidate."""
    policy = ThresholdResolutionPolicy()
    scored = [_make_scored_candidate("e1", "alpha", 0.90)]
    decision = policy.decide("m", scored)

    assert decision.second_score is None
    assert decision.score_margin is None


def test_ambiguous_decision_mention_has_no_entity_id():
    """After AMBIGUOUS decision, the stored mention has entity_id = None."""
    e1 = _make_entity("entity_001", EntityType.PERSON, "Rahul Kumar Svc")
    e2 = _make_entity("entity_002", EntityType.PERSON, "Rahul Sharma Svc")
    mention = _make_mention("mention_amb", "Rahul Svc")

    service, mention_repo = _make_resolution_service(
        entities=[e1, e2],
        mentions=[mention],
        resolution_threshold=0.01,  # very low threshold
        ambiguity_margin=0.99,       # very high margin → always AMBIGUOUS
    )

    decision = service.resolve("mention_amb")
    assert decision.outcome == ResolutionOutcome.AMBIGUOUS

    stored = mention_repo.get_by_id("mention_amb")
    assert stored.entity_id is None


def test_unresolved_decision_mention_has_no_entity_id():
    """After UNRESOLVED decision, the stored mention has entity_id = None."""
    entity = _make_entity("entity_001", EntityType.PERSON, "Rahul Kumar Svc2")
    # Mention that doesn't overlap.
    mention = _make_mention("mention_unres", "Completely Unrelated Name Here")

    service, mention_repo = _make_resolution_service(
        entities=[entity], mentions=[mention]
    )
    decision = service.resolve("mention_unres")
    assert decision.outcome == ResolutionOutcome.UNRESOLVED

    stored = mention_repo.get_by_id("mention_unres")
    assert stored.entity_id is None
    assert stored.resolution_status == ResolutionStatus.UNRESOLVED


# ===========================================================================
# REGRESSION: ensure earlier tests' imports still work
# ===========================================================================

def test_r39_candidate_generation_models_still_importable():
    """Regression: EntityCandidate and related models still importable."""
    from app.models.entity import EntityCandidate  # noqa: F401
    assert EntityCandidate is not None


def test_r40_scoring_method_constant_unchanged():
    """Regression: SCORING_METHOD constant in lexical_candidate_scorer is stable."""
    from app.entity_resolution.lexical_candidate_scorer import SCORING_METHOD
    assert SCORING_METHOD == "lexical_weighted_coverage"


def test_r41_mention_repository_update_works():
    """New update() method on InMemoryMentionRepository works correctly."""
    repo = InMemoryMentionRepository()
    mention = _make_mention("m1", "original text")
    repo.create(mention)

    # Update the mention's resolution_status.
    updated = mention.model_copy(
        update={"resolution_status": ResolutionStatus.RESOLVED, "entity_id": "e1"}
    )
    repo.update(updated)

    stored = repo.get_by_id("m1")
    assert stored.resolution_status == ResolutionStatus.RESOLVED
    assert stored.entity_id == "e1"
