"""Proactive organisation intelligence scans (Stage 34, Part E).

Gap addressed: attention, insight, and action computation previously ran
only when queried.  This module provides the SAFE proactive foundation on
top of the existing durable worker/job architecture — no new scheduler
library, no second intelligence engine, no distributed claims.

Architecture
------------
* ``ORGANISATION_INTELLIGENCE_SCAN`` is one explicit background operation.
  The worker claims the durable job, derives the organisation scope SOLELY
  from ``job.organisation_id`` (never from request/runtime state), builds
  the standard tenant service graph, and invokes the EXISTING
  deterministic intelligence services (attention, insights, actions,
  organisation changes, portfolio).  The scanner decides WHEN/WHICH; the
  services decide WHAT counts as a signal.
* Persistence is the durable job row itself (E5): the handler writes a JSON
  ``result_summary`` plus the observed source watermark
  (``processing_revision``) via ``record_scan_result`` before success, so a
  scan survives worker/process restart and retry with identical results.
* Idempotency (E3): signal IDs are deterministic functions of source state
  (insight_id, attention_id, action_id, change_id contain no wall-clock),
  so re-executing a scan over unchanged state reproduces the same summary.
  Scheduler-level duplicate prevention comes from time-bucketed job IDs
  plus ``enqueue``'s ON CONFLICT DO NOTHING semantics.
* Staleness (E4): the scan always recomputes (STALE_* signals are
  time-dependent, so a watermark shortcut could miss newly-stale
  entities).  When nothing changed, the scan still records SUCCEEDED
  completion with an empty new-signal list — no duplicate alert, previous
  derived intelligence preserved (read models recompute from source).
* Product rule (E9): a scan NEVER alerts merely because it ran.  Only
  signal IDs absent from the previous successful scan are "new".
  The very first scan establishes the baseline (new list empty).
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from app.models.background_job import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
)

logger = logging.getLogger(__name__)

SCAN_JOB_TYPE = BackgroundJobType.ORGANISATION_INTELLIGENCE_SCAN
SCAN_SUMMARY_SCHEMA_VERSION = 1
# Bounds keep the durable summary small no matter how large an org grows.
# IDs are short hashes; 5000 IDs ≈ 100 KB worst case in a TEXT column.
MAX_SIGNAL_IDS = 5000
MAX_NEW_SIGNAL_IDS = 200

_OUTSTANDING = {
    BackgroundJobStatus.PENDING,
    BackgroundJobStatus.RUNNING,
    BackgroundJobStatus.RETRY_WAITING,
}


def scan_job_id(organisation_id: str, bucket: int) -> str:
    """Deterministic job ID for one organisation's scan in one time bucket."""
    return f"{SCAN_JOB_TYPE.value}:{organisation_id}:{bucket}"


def time_bucket(now: datetime, interval_seconds: float) -> int:
    return int(now.timestamp() // interval_seconds)


@dataclass(frozen=True)
class ProactiveScanResult:
    organisation_id: str
    watermark: int
    signal_ids: tuple = ()
    new_signal_ids: tuple = ()
    previous_completed_at: str | None = None
    completed_at: str = ""
    truncated: bool = False


def _parse_summary(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _signal_set_from_services(graph: dict, now: datetime) -> list[str]:
    """Collect deterministic signal IDs using ONLY existing services."""
    repos = graph["repos"]
    intel = graph["intelligence"]
    signals: set[str] = set()

    for change in intel["changes"].get_changes(current_time=now):
        signals.add(f"chg:{change.change_id}")
    for item in intel["attention"].get_attention(current_time=now):
        signals.add(f"att:{item.attention_id}")
    for entity in repos.entities.list_entities():
        entity_id = entity.entity_id
        for insight in intel["insights"].get_entity_insights(entity_id, now):
            signals.add(f"ins:{insight.insight_id}")
        for action in intel["actions"].get_entity_actions(entity_id, now):
            signals.add(f"act:{action.action_id}")
    # NOTE: no portfolio aggregate signal.  The portfolio snapshot embeds
    # wall-clock evaluation timestamps, so hashing it would manufacture a
    # "new" signal on every scan.  Portfolio-level shifts always manifest
    # as member insight/attention/change/action ID changes, which are the
    # exact new-signal set — nothing is lost by excluding the aggregate.
    return sorted(signals)


def _source_watermark(repos) -> int:
    """Highest meeting source revision in this organisation's scope (0 = none)."""
    watermark = 0
    for meeting in repos.meetings.list_meetings():
        try:
            revision = int(getattr(meeting, "source_revision", 1) or 1)
        except (TypeError, ValueError):
            continue
        if revision > watermark:
            watermark = revision
    return watermark


class ProactiveIntelligenceScanner:
    """Executes one ORGANISATION_INTELLIGENCE_SCAN job to SUCCEEDED state."""

    def __init__(self, *, job_repository, build_graph) -> None:
        """
        job_repository: UNSCOPED durable job repo (reads history, writes
            this job's summary).
        build_graph: callable organisation_id -> tenant graph dict with
            "repos" and "intelligence" (changes/attention/insights/actions/
            portfolio), i.e. main._build_tenant_graph.
        """
        self._jobs = job_repository
        self._build_graph = build_graph

    def _previous_success(self, organisation_id: str) -> BackgroundJob | None:
        candidates = [
            job
            for job in self._jobs.list(
                status=BackgroundJobStatus.SUCCEEDED,
                organisation_id=organisation_id,
            )
            if job.job_type == SCAN_JOB_TYPE and job.completed_at is not None
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda job: job.completed_at)

    def run_scan(self, job: BackgroundJob, now: datetime | None = None) -> ProactiveScanResult:
        """Run the scan for the job's durable organisation scope.

        Raises on unexpected failures so the worker's retry/fail semantics
        apply (E8): never fabricates success, never erases good state.
        """
        moment = now or datetime.now(timezone.utc)
        organisation_id = getattr(job, "organisation_id", None) or "default"
        graph = self._build_graph(organisation_id)

        watermark = _source_watermark(graph["repos"])
        signal_ids = _signal_set_from_services(graph, moment)
        truncated = len(signal_ids) > MAX_SIGNAL_IDS
        signal_ids = signal_ids[:MAX_SIGNAL_IDS]

        previous = self._previous_success(organisation_id)
        previous_ids: set[str] = set()
        previous_completed_at: str | None = None
        if previous is not None:
            previous_completed_at = (
                previous.completed_at.isoformat() if previous.completed_at else None
            )
            parsed = _parse_summary(previous.result_summary)
            if parsed is not None:
                previous_ids = set(parsed.get("signal_ids", []))

        if previous is None:
            # First scan establishes the baseline — never an alert burst.
            new_ids: list[str] = []
        else:
            new_ids = sorted(set(signal_ids) - previous_ids)[:MAX_NEW_SIGNAL_IDS]

        result = ProactiveScanResult(
            organisation_id=organisation_id,
            watermark=watermark,
            signal_ids=tuple(signal_ids),
            new_signal_ids=tuple(new_ids),
            previous_completed_at=previous_completed_at,
            completed_at=moment.isoformat(),
            truncated=truncated,
        )
        summary = json.dumps(
            {
                "schema_version": SCAN_SUMMARY_SCHEMA_VERSION,
                "watermark": watermark,
                "signal_count": len(signal_ids),
                "signal_ids": list(signal_ids),
                "new_signal_ids": list(new_ids),
                "previous_completed_at": previous_completed_at,
                "completed_at": result.completed_at,
                "truncated": truncated,
            },
            sort_keys=True,
        )
        self._jobs.record_scan_result(
            job.job_id,
            summary,
            processing_revision=str(watermark),
            worker_id=getattr(job, "worker_id", None),
        )
        logger.info(
            "proactive_scan_complete org=%s watermark=%s signals=%d new=%d",
            organisation_id,
            watermark,
            len(signal_ids),
            len(new_ids),
        )
        return result


def plan_proactive_scans(
    *,
    job_repository,
    auth_repository,
) -> list[str]:
    """Return organisation IDs that need a scan job enqueued now.

    Pure planning step (no writes): an org is eligible when it has no
    OUTSTANDING scan job.  History rows (SUCCEEDED/FAILED) never block a
    new scan — per-cycle job IDs keep history while preventing duplicates.
    """
    eligible: list[str] = []
    try:
        org_ids = auth_repository.list_organisation_ids()
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        logger.warning("proactive_scan_discovery_failed: %s", type(exc).__name__)
        return []
    for org_id in org_ids:
        outstanding = [
            job
            for job in job_repository.list(organisation_id=org_id)
            if job.job_type == SCAN_JOB_TYPE and job.status in _OUTSTANDING
        ]
        if not outstanding:
            eligible.append(org_id)
    return eligible


def build_scan_job(
    organisation_id: str,
    bucket: int,
    *,
    now: datetime | None = None,
    max_attempts: int = 3,
) -> BackgroundJob:
    """Build (not enqueue) one scan job for an org/bucket pair."""
    return BackgroundJob(
        job_id=scan_job_id(organisation_id, bucket),
        job_type=SCAN_JOB_TYPE,
        status=BackgroundJobStatus.PENDING,
        created_at=now or datetime.now(timezone.utc),
        payload_id=organisation_id,
        max_attempts=max_attempts,
        organisation_id=organisation_id,
    )
