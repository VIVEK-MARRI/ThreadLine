from datetime import datetime, timezone

from app.models.entity import EntityType, ResolutionStatus
from app.models.meeting import Meeting
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.meeting_repository import InMemoryMeetingRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.services.entity_observation_service import EntityObservationService
from app.services.entity_service import EntityService


def test_observation_resolves_declared_participant_without_creating_entity():
    meetings = InMemoryMeetingRepository()
    entities = InMemoryEntityRepository()
    mentions = InMemoryMentionRepository()
    entity, _ = EntityService(entities, mentions).create_entity(EntityType.PERSON, "Rahul Kumar")
    meetings.save(Meeting(
        meeting_id="meeting-observation",
        title="Payments",
        transcript="Rahul Kumar said the payment API is still blocked.",
        meeting_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        participants=["Rahul Kumar"],
    ))

    EntityObservationService(meetings, entities, mentions).observe_meeting("meeting-observation")

    observed = mentions.list_by_meeting_id("meeting-observation")
    assert len(observed) == 1
    assert observed[0].entity_id == entity.entity_id
    assert observed[0].resolution_status == ResolutionStatus.RESOLVED
    assert len(entities.list_entities()) == 1


def test_unknown_declared_participant_remains_unresolved():
    meetings = InMemoryMeetingRepository()
    entities = InMemoryEntityRepository()
    mentions = InMemoryMentionRepository()
    meetings.save(Meeting(
        meeting_id="meeting-unresolved",
        title="Payments",
        transcript="Rahul said the payment API is still blocked.",
        meeting_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        participants=["Rahul"],
    ))

    EntityObservationService(meetings, entities, mentions).observe_meeting("meeting-unresolved")

    observed = mentions.list_by_meeting_id("meeting-unresolved")
    assert len(observed) == 1
    assert observed[0].entity_id is None
    assert observed[0].resolution_status == ResolutionStatus.UNRESOLVED
    assert entities.list_entities() == []