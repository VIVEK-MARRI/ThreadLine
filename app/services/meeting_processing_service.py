"""Checkpointed, source-first meeting processing workflow."""

from enum import StrEnum
from datetime import datetime, timezone
from typing import Callable

from app.models.background_job import BackgroundJob
from app.repositories.background_job_repository import AbstractBackgroundJobRepository
from app.services.background_worker_service import PermanentJobError


class ProcessingStage(StrEnum):
    INGESTED = "INGESTED"
    EXTRACTED = "EXTRACTED"
    RESOLVED = "RESOLVED"
    RELATIONSHIPS_PERSISTED = "RELATIONSHIPS_PERSISTED"
    DERIVED_INTELLIGENCE = "DERIVED_INTELLIGENCE"
    SEMANTIC_INDEXED = "SEMANTIC_INDEXED"
    COMPLETED = "COMPLETED"


class MeetingProcessingService:
    """Run finite processing stages and persist a checkpoint after each one."""

    _stages = (
        ProcessingStage.EXTRACTED,
        ProcessingStage.RESOLVED,
        ProcessingStage.RELATIONSHIPS_PERSISTED,
        ProcessingStage.DERIVED_INTELLIGENCE,
        ProcessingStage.SEMANTIC_INDEXED,
        ProcessingStage.COMPLETED,
    )

    def __init__(self, repository: AbstractBackgroundJobRepository, handlers: dict[ProcessingStage, Callable[[str], None]]) -> None:
        self._repository = repository
        self._handlers = handlers

    def process(self, job: BackgroundJob) -> None:
        current_index = -1
        if job.stage:
            try:
                current_index = self._stages.index(ProcessingStage(job.stage))
            except ValueError:
                current_index = -1
        for stage in self._stages[current_index + 1:]:
            if stage == ProcessingStage.COMPLETED:
                self._repository.checkpoint(job.job_id, stage.value, job.worker_id, datetime.now(timezone.utc))
                continue
            handler = self._handlers.get(stage)
            if handler is None:
                raise PermanentJobError(f"missing processing handler for stage {stage.value}")
            handler(job.payload_id)
            self._repository.checkpoint(job.job_id, stage.value, job.worker_id, datetime.now(timezone.utc))
