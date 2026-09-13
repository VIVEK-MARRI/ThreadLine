"""Create conservative entity observations from authoritative meeting data.

Pipeline
--------
raw transcript
→ structured participant + lexical entity discovery
→ observed entity mentions (UNRESOLVED at creation)
→ candidate generation (lexical, read-only)
→ candidate scoring (lexical, read-only)
→ resolution policy (RESOLVED / AMBIGUOUS / UNRESOLVED)
→ durable mention state

An observation is NEVER automatically a canonical entity.  Identity is only
assigned when the shared lexical candidate-scoring + threshold/margin policy
produces a RESOLVED decision.  Multiple strong candidates produce AMBIGUOUS,
no strong candidate produces UNRESOLVED.  No canonical entities are invented,
no semantic similarity is used as identity, and ambiguous matches are never
forced.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Callable

from app.models.entity import EntityMention, EntityType, ResolutionStatus

logger = logging.getLogger(__name__)


def _window(transcript: str, start: int, end: int, before: int = 80, after: int = 120) -> str:
    return transcript[max(0, start - before):end + after]


class EntityObservationService:
    """Observe transcript references without inventing canonical entities."""

    def __init__(
        self,
        meeting_repository,
        entity_repository,
        mention_repository,
        scoring_service=None,
        resolution_policy=None,
        resolution_service=None,
    ) -> None:
        self._meetings = meeting_repository
        self._entities = entity_repository
        self._mentions = mention_repository
        self._scoring_service = scoring_service
        self._resolution_policy = resolution_policy
        self._resolution_service = resolution_service
        self._ownership_checker: Callable[[], None] | None = None

    def set_ownership_checker(self, checker: Callable[[], None] | None) -> None:
        self._ownership_checker = checker

    def _assert_owned(self) -> None:
        if self._ownership_checker is not None:
            self._ownership_checker()

    def _resolution_components(self):
        if self._resolution_service is not None:
            return self._resolution_service, None, None
        from app.entity_resolution.lexical_candidate_generator import LexicalCandidateGenerator
        from app.entity_resolution.lexical_candidate_scorer import LexicalCandidateScorer
        from app.entity_resolution.resolution_policy import ThresholdResolutionPolicy
        from app.services.candidate_scoring_service import CandidateScoringService

        scoring = self._scoring_service
        if scoring is None:
            scoring = CandidateScoringService(
                self._mentions, self._entities,
                LexicalCandidateGenerator(), LexicalCandidateScorer(),
            )
        policy = self._resolution_policy
        if policy is None:
            policy = ThresholdResolutionPolicy()
        return None, scoring, policy

    def observe_meeting(self, meeting_id: str) -> list[EntityMention]:
        meeting = self._meetings.get_by_id(meeting_id)
        if meeting is None:
            raise ValueError(f"Meeting '{meeting_id}' not found")
        source_revision = int(getattr(meeting, "source_revision", 1) or 1)

        from app.repositories.entity_repository import _normalize as _norm

        existing = set()
        for mention in self._mentions.list_by_meeting_id(meeting_id):
            try:
                existing.add((
                    mention.entity_type,
                    _norm(mention.text),
                    mention.source_text,
                    int(getattr(mention, "source_revision", 1) or 1),
                ))
            except Exception:
                continue

        # Collect deterministic observation proposals: (entity_type, text, source_text)
        proposals: list[tuple[EntityType, str, str]] = []

        # 1. Declared participants verbatim in transcript (PERSON observations).
        for participant in meeting.participants:
            if not participant or not participant.strip():
                continue
            match = re.search(
                rf"(?<!\w){re.escape(participant)}(?!\w)",
                meeting.transcript,
                flags=re.IGNORECASE,
            )
            if match is None:
                continue
            source_text = _window(meeting.transcript, match.start(), match.end())
            proposals.append((EntityType.PERSON, participant, source_text))

        # 2. Known canonical entities / aliases verbatim in transcript.
        # Discovery only — identity is decided by the scoring policy below,
        # never by this substring scan alone.
        try:
            known_entities = self._entities.list_entities()
        except Exception:
            known_entities = []
        for entity in known_entities:
            representations = [entity.canonical_name, *list(entity.aliases or [])]
            for representation in representations:
                if not representation or not representation.strip():
                    continue
                try:
                    match = re.search(
                        rf"(?<!\w){re.escape(representation)}(?!\w)",
                        meeting.transcript,
                        flags=re.IGNORECASE,
                    )
                except re.error:
                    continue
                if match is None:
                    continue
                source_text = _window(meeting.transcript, match.start(), match.end())
                proposals.append((entity.entity_type, representation, source_text))

        resolution_service, scoring_service, policy = self._resolution_components()

        observed: list[EntityMention] = []
        for entity_type, text, source_text in proposals:
            try:
                norm_text = _norm(text)
            except Exception:
                norm_text = text
            key = (entity_type, norm_text, source_text, source_revision)
            if key in existing:
                continue
            self._assert_owned()
            mention = EntityMention(
                mention_id=str(uuid.uuid4()),
                entity_type=entity_type,
                text=text,
                meeting_id=meeting_id,
                source_text=source_text,
                entity_id=None,
                resolution_status=ResolutionStatus.UNRESOLVED,
                created_at=datetime.now(tz=timezone.utc),
                source_revision=source_revision,
            )
            self._mentions.create(mention)
            existing.add(key)

            # Candidate generation → scoring → policy determines final state.
            # Never invents entities; never forces ambiguous matches.
            try:
                if resolution_service is not None:
                    decision = resolution_service.resolve(mention.mention_id)
                    refreshed = self._mentions.get_by_id(mention.mention_id)
                    if refreshed is not None:
                        mention = refreshed
                else:
                    assert scoring_service is not None and policy is not None
                    _, scored = scoring_service.get_scored_candidates(mention.mention_id)
                    decision = policy.decide(mention_id=mention.mention_id, scored_candidates=scored)
                    if decision.outcome.value == "RESOLVED":
                        candidate_ids = {sc.entity_id for sc in scored}
                        if decision.selected_entity_id in candidate_ids:
                            updated = mention.model_copy(update={
                                "entity_id": decision.selected_entity_id,
                                "resolution_status": ResolutionStatus.RESOLVED,
                            })
                            self._assert_owned()
                            self._mentions.update(updated)
                            mention = updated
                    elif decision.outcome.value == "AMBIGUOUS":
                        updated = mention.model_copy(update={
                            "entity_id": None,
                            "resolution_status": ResolutionStatus.AMBIGUOUS,
                        })
                        self._assert_owned()
                        self._mentions.update(updated)
                        mention = updated
                    else:
                        # UNRESOLVED — leave as-is, entity_id stays None.
                        pass
            except Exception:
                logger.exception("EntityObservationService: resolution failed for mention %s", mention.mention_id)
            observed.append(mention)

        return observed
