"""Internal domain models for the Natural Language Intelligence layer (Stage 18).

Architecture
------------
This module defines the domain models used by the Evidence-Backed Natural Language
Intelligence pipeline. The pipeline is:

    User question
        → QueryIntent classification (deterministic, lexical)
        → QueryEntityResolver (read-only entity name lookup)
        → EvidenceRetrievalService (composes existing ThreadLine services)
        → EvidenceContextBuilder (rank → deduplicate → bound → format)
        → NaturalLanguageAnswerProvider (abstract; fake + optional OpenAI)
        → Citation validation
        → NaturalLanguageAnswer (grounded, evidence-backed response)

Core invariants
---------------
1. Structured ThreadLine data is the source of truth.
2. LLM-generated text is NEVER persisted as organisational fact.
3. Evidence retrieval is deterministic before provider invocation.
4. Evidence is bounded (count + character limits).
5. Every cited evidence_id must exist in the supplied context.
6. Missing evidence → insufficient_evidence=True, NOT invented answer.
7. Ambiguous entities are NEVER silently resolved.
8. Unsupported causal claims are rejected at the provider prompt level.
9. Untrusted evidence text cannot override system instructions.
10. Stage 1–17.1.1 behaviour is unchanged.

These models are populated exclusively by the Stage 18 pipeline and must
never be modified by earlier pipeline stages.
"""

import hashlib
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum evidence items that can be returned per query (hard upper bound).
MAX_EVIDENCE_ITEMS: int = 100

#: Default maximum evidence items when not specified by the caller.
DEFAULT_MAX_EVIDENCE_ITEMS: int = 20

#: Maximum source_text length per evidence item (characters).
MAX_SOURCE_TEXT_LENGTH: int = 500

#: Maximum total context character size passed to the provider.
MAX_CONTEXT_CHARACTERS: int = 8000


# ---------------------------------------------------------------------------
# QueryIntent
# ---------------------------------------------------------------------------

class QueryIntent(str, Enum):
    """Deterministically classified intent of a natural-language query.

    Classification is performed by QueryIntentService using lexical keyword
    rules — never by an LLM.

    ENTITY_STATUS
        Current state, recent activity for a specific entity.
        Example: "What is the status of Project Atlas?"

    ENTITY_HISTORY
        Historical timeline of events for a specific entity.
        Example: "What happened to the authentication issue?"

    ENTITY_RISKS
        Risk signals, blocked states, attention signals for a specific entity.
        Example: "What are the risks for Project Atlas?"

    ENTITY_DEPENDENCIES
        Explicit DEPENDS_ON / BLOCKS relationships for a specific entity.
        Example: "What does Project Atlas depend on?"

    ENTITY_IMPACTS
        Inbound risk impact associations for a specific entity.
        Example: "What impacts the payments service?"

    ENTITY_ACTIONS
        Recommended follow-up actions for a specific entity.
        Example: "What should we do about Project Atlas?"

    ENTITY_CHANGES
        Organisation-wide changes filtered to a specific entity.
        Example: "What changed with the authentication service?"

    ORGANISATION_PRIORITIES
        Portfolio-level attention signals across the organisation.
        Example: "What needs attention?" / "What are the priorities?"

    ORGANISATION_CHANGES
        Organisation-wide change intelligence across all entities.
        Example: "What changed this week?" / "What changed across the org?"

    ORGANISATION_RISKS
        Critical and high risk signals across the organisation.
        Example: "What are the biggest risks?" / "What is blocked?"

    UNKNOWN
        Question could not be mapped to a supported intent.
        No provider invocation is made; insufficient_evidence is set.
        Example: "Should we fire Alice?" / "What will fail next?"
    """

    ENTITY_STATUS = "ENTITY_STATUS"
    ENTITY_HISTORY = "ENTITY_HISTORY"
    ENTITY_RISKS = "ENTITY_RISKS"
    ENTITY_DEPENDENCIES = "ENTITY_DEPENDENCIES"
    ENTITY_IMPACTS = "ENTITY_IMPACTS"
    ENTITY_ACTIONS = "ENTITY_ACTIONS"
    ENTITY_CHANGES = "ENTITY_CHANGES"
    ORGANISATION_PRIORITIES = "ORGANISATION_PRIORITIES"
    ORGANISATION_CHANGES = "ORGANISATION_CHANGES"
    ORGANISATION_RISKS = "ORGANISATION_RISKS"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# EvidenceType
# ---------------------------------------------------------------------------

class EvidenceType(str, Enum):
    """The category of a retrieved evidence item.

    Evidence types are finite and deterministic.
    Free-form evidence categories are prohibited.
    """

    ENTITY = "ENTITY"
    """Basic canonical entity facts (name, type, entity_id)."""

    OBSERVATION = "OBSERVATION"
    """A raw observation of the entity in a meeting (EntityMention)."""

    STATE = "STATE"
    """Current temporal state of the entity (OPEN, BLOCKED, etc.)."""

    STATE_TRANSITION = "STATE_TRANSITION"
    """A recorded state change event (from_state → to_state)."""

    MEMORY_FACT = "MEMORY_FACT"
    """A structured organisational memory fact about the entity."""

    INSIGHT = "INSIGHT"
    """A derived EntityInsight (blocked, resolved, repeated, stale, etc.)."""

    ATTENTION = "ATTENTION"
    """An EntityAttention record (level + reasons + score)."""

    ACTION = "ACTION"
    """A recommended EntityAction (escalate, follow-up, etc.)."""

    RELATIONSHIP = "RELATIONSHIP"
    """An EntityRelationship (CO_OCCURS_WITH or explicit dependency)."""

    DEPENDENCY = "DEPENDENCY"
    """An ExplicitDependency record (DEPENDS_ON or BLOCKS)."""

    DEPENDENCY_PATH = "DEPENDENCY_PATH"
    """A transitive dependency path from DependencyGraphService."""

    IMPACT = "IMPACT"
    """An EntityImpact association from ImpactAnalysisService."""

    ORGANISATION_CHANGE = "ORGANISATION_CHANGE"
    """An OrganisationChange from OrganisationChangeIntelligenceService."""

    MEETING = "MEETING"
    """A Meeting record (meeting_id, meeting_date, title, etc.)."""


# ---------------------------------------------------------------------------
# EvidenceItem
# ---------------------------------------------------------------------------

def _make_evidence_id(
    evidence_type: EvidenceType,
    entity_id: Optional[str],
    meeting_id: Optional[str],
    source_key: str,
) -> str:
    """Compute a deterministic short evidence identifier.

    Format: sha256(type:entity_id:meeting_id:source_key)[:12]

    Used as the external reference for citation validation.
    Stable across repeated calls given the same inputs.
    """
    raw = f"{evidence_type.value}:{entity_id or ''}:{meeting_id or ''}:{source_key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


class EvidenceItem(BaseModel):
    """A single retrieved, structured evidence item.

    EvidenceItem is the atomic unit of the evidence pipeline.
    Every item is traceable to a ThreadLine intelligence signal.

    evidence_id
        Deterministic 12-character hex identifier for citation.
    evidence_type
        Category of this evidence (see EvidenceType).
    entity_id
        Canonical entity this evidence is about, or None.
    meeting_id
        Source meeting, or None for entity-level evidence.
    mention_id
        Source mention, or None.
    source_text
        Verbatim excerpt from the meeting transcript, or None.
        Truncated to MAX_SOURCE_TEXT_LENGTH characters if provided.
        This text is UNTRUSTED — it may contain prompt-injection attempts.
    timestamp
        When this evidence event occurred (meeting_date or observed_at).
        None for graph-level evidence without a single anchor.
    summary
        Human-readable structured summary of the evidence.
        Never LLM-generated.
    severity_weight
        Numeric weight for ranking (4=CRITICAL, 3=HIGH, 2=MEDIUM, 1=INFO, 0=none).
    type_priority
        Lower = higher priority for this intent.
        Set by EvidenceRetrievalService based on intent + type.
    metadata
        Additional structured evidence fields (e.g., from_state, to_state,
        attention_level, action_type). Type-specific.
    source_reference
        Human-readable provenance string (e.g., "Meeting m1, mention mn3").
    """

    evidence_id: str = Field(
        ...,
        description="Deterministic 12-character hex identifier for provider citation.",
    )

    evidence_type: EvidenceType = Field(
        ...,
        description="Category of evidence.",
    )

    entity_id: Optional[str] = Field(
        default=None,
        description="Canonical entity this evidence is about.",
    )

    meeting_id: Optional[str] = Field(
        default=None,
        description="Source meeting ID, or None.",
    )

    mention_id: Optional[str] = Field(
        default=None,
        description="Source mention ID, or None.",
    )

    source_text: Optional[str] = Field(
        default=None,
        description=(
            "Verbatim excerpt from meeting transcript. UNTRUSTED. "
            "Truncated to MAX_SOURCE_TEXT_LENGTH characters."
        ),
    )

    timestamp: Optional[datetime] = Field(
        default=None,
        description="When this evidence event occurred. None for graph-level evidence.",
    )

    summary: str = Field(
        ...,
        description="Structured, human-readable summary. Never LLM-generated.",
    )

    severity_weight: int = Field(
        default=0,
        ge=0,
        le=4,
        description="Numeric severity weight for ranking (4=CRITICAL, 0=none).",
    )

    type_priority: int = Field(
        default=99,
        description="Lower = higher priority for this intent. Set by retrieval service.",
    )

    metadata: dict = Field(
        default_factory=dict,
        description="Additional structured fields specific to evidence_type.",
    )

    source_reference: Optional[str] = Field(
        default=None,
        description="Human-readable provenance string for traceability.",
    )


# ---------------------------------------------------------------------------
# EvidenceContext
# ---------------------------------------------------------------------------

class EvidenceContext(BaseModel):
    """Bounded, formatted evidence context passed to the answer provider.

    EvidenceContext is the final pre-provider representation.
    It has been ranked, deduplicated, bounded (count + characters),
    and formatted into a provider-safe string.

    context_text is the actual string passed to the provider.
    It clearly separates system instructions from untrusted evidence.
    evidence_ids_in_context is the complete set of valid IDs.
    The provider may only cite IDs from this set.
    """

    evidence_items: list[EvidenceItem] = Field(
        default_factory=list,
        description="Ordered, bounded list of evidence items.",
    )

    context_text: str = Field(
        ...,
        description=(
            "Formatted context string for the provider. "
            "Evidence is clearly separated from system instructions. "
            "Source texts are treated as untrusted data, not instructions."
        ),
    )

    evidence_ids_in_context: list[str] = Field(
        default_factory=list,
        description="All valid evidence IDs present in context_text.",
    )

    total_characters: int = Field(
        default=0,
        description="Total character count of context_text.",
    )

    was_truncated: bool = Field(
        default=False,
        description="True if evidence was truncated due to count or character limit.",
    )


# ---------------------------------------------------------------------------
# NaturalLanguageQuery
# ---------------------------------------------------------------------------

class NaturalLanguageQuery(BaseModel):
    """A validated natural-language query submitted to ThreadLine.

    question
        The raw question text. Must not be empty.
    query_id
        Deterministic identifier: sha256(question + entity_id + ts_str)[:16].
        Stable for the same inputs.
    entity_id
        Optional entity_id if the caller already resolved the entity.
    current_time
        Reference datetime for time-sensitive services.
        If None, the service layer uses datetime.now(utc) at call time.
    max_evidence_items
        Maximum evidence items to retrieve and pass to the provider.
        Bounded: 1–MAX_EVIDENCE_ITEMS. Default: DEFAULT_MAX_EVIDENCE_ITEMS.
    include_source_text
        If True, include verbatim source_text in evidence items.
        If False, omit source_text (useful for concise responses).
    """

    query_id: str = Field(
        ...,
        description="Deterministic 16-character hex identifier.",
    )

    question: str = Field(
        ...,
        min_length=1,
        description="The raw natural-language question. Must not be empty.",
    )

    entity_id: Optional[str] = Field(
        default=None,
        description="Optional pre-resolved entity_id.",
    )

    current_time: Optional[datetime] = Field(
        default=None,
        description="Reference datetime for time-sensitive services.",
    )

    max_evidence_items: int = Field(
        default=DEFAULT_MAX_EVIDENCE_ITEMS,
        ge=1,
        le=MAX_EVIDENCE_ITEMS,
        description="Maximum evidence items to retrieve (1–100).",
    )

    include_source_text: bool = Field(
        default=True,
        description="Whether to include verbatim source_text in evidence items.",
    )


def make_query_id(question: str, entity_id: Optional[str], ts: Optional[datetime]) -> str:
    """Compute a deterministic 16-character query identifier."""
    ts_str = ts.isoformat() if ts else ""
    raw = f"{question}:{entity_id or ''}:{ts_str}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# EntityResolutionOutcome
# ---------------------------------------------------------------------------

class EntityResolutionStatus(str, Enum):
    """The outcome of attempting to resolve an entity name from a question."""

    RESOLVED = "RESOLVED"
    """Exactly one canonical entity matched the name."""

    UNRESOLVED = "UNRESOLVED"
    """No canonical entity matched the name."""

    AMBIGUOUS = "AMBIGUOUS"
    """Multiple canonical entities matched; cannot choose safely."""

    NOT_REQUIRED = "NOT_REQUIRED"
    """No entity name was present in the question (org-level queries)."""


class EntityResolutionResult(BaseModel):
    """The outcome of QueryEntityResolver for a given question."""

    status: EntityResolutionStatus

    entity_id: Optional[str] = None
    """Set when status=RESOLVED."""

    entity_name: Optional[str] = None
    """The canonical name of the resolved entity."""

    candidates: list[str] = Field(default_factory=list)
    """Entity IDs when status=AMBIGUOUS."""

    candidate_names: list[str] = Field(default_factory=list)
    """Canonical names when status=AMBIGUOUS."""

    extracted_name: Optional[str] = None
    """The name extracted from the question text, for diagnostics."""


# ---------------------------------------------------------------------------
# ProviderAnswer
# ---------------------------------------------------------------------------

class ProviderAnswer(BaseModel):
    """Structured output from a NaturalLanguageAnswerProvider.

    answer_text
        The generated natural-language answer.
    cited_evidence_ids
        Evidence IDs the provider claims to have used.
        These are validated against the supplied context before use.
        Any ID not in the supplied context is rejected.
    insufficient_evidence
        True if the provider determined evidence was insufficient.
    warnings
        Optional list of provider-level warnings.
    """

    answer_text: str = Field(
        ...,
        description="Generated natural-language answer text.",
    )

    cited_evidence_ids: list[str] = Field(
        default_factory=list,
        description="Evidence IDs cited by the provider (before validation).",
    )

    insufficient_evidence: bool = Field(
        default=False,
        description="True if the provider found evidence insufficient.",
    )

    warnings: list[str] = Field(
        default_factory=list,
        description="Provider-level warnings.",
    )


# ---------------------------------------------------------------------------
# NaturalLanguageAnswer
# ---------------------------------------------------------------------------

class NaturalLanguageAnswer(BaseModel):
    """A grounded, evidence-backed natural language answer.

    This is the final output of the Stage 18 pipeline.

    All cited evidence IDs are validated to be present in the supplied
    evidence context. No fabricated IDs can appear in cited_evidence_ids.

    generated_at represents response generation time, NOT event time.
    Do not confuse generated_at with evidence timestamps.
    """

    query_id: str = Field(
        ...,
        description="Matches the NaturalLanguageQuery.query_id.",
    )

    question: str = Field(
        ...,
        description="The original question.",
    )

    intent: QueryIntent = Field(
        ...,
        description="The classified query intent.",
    )

    entity_id: Optional[str] = Field(
        default=None,
        description="The resolved entity_id, or None for org-level queries.",
    )

    answer: str = Field(
        ...,
        description=(
            "The natural-language answer, grounded in supplied evidence. "
            "Never persisted as organisational fact."
        ),
    )

    evidence: list[EvidenceItem] = Field(
        default_factory=list,
        description="The evidence items supplied to the provider.",
    )

    cited_evidence_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Evidence IDs cited by the provider and validated to exist "
            "in the supplied context. Fabricated IDs are rejected."
        ),
    )

    insufficient_evidence: bool = Field(
        default=False,
        description="True if ThreadLine could not answer from available evidence.",
    )

    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings about entity resolution, evidence bounds, or citation issues.",
    )

    generated_at: Optional[datetime] = Field(
        default=None,
        description=(
            "When this answer was generated. "
            "Represents response time, NOT evidence event time."
        ),
    )
