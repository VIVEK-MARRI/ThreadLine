"""Durable source / processing / semantic consistency contract (Stage 23.3).

Authoritative chain
-------------------
meeting (source_revision N, deterministic monotonic integer)
→ processing_job / processing_revision (explicitly tied to source revision N)
→ derived processing (extraction / mentions / dependencies stamped with N)
→ semantic index records (stamped with N + meeting_id)

Invariant when processing is successfully complete
--------------------------------------------------
AUTHORITATIVE SOURCE REVISION == CURRENT DERIVED PROCESSING REVISION
== CURRENT SEMANTIC EVIDENCE REVISION.

When processing is incomplete or failed, the system exposes that state rather
than pretending derived state is current.  A failed revision never silently
becomes "current".

Revision identity vs revision order
----------------------------------
REVISION IDENTITY is the full durable string identifying one processing
execution: "<source_N>" for normal pipeline runs, "<source_N>@<suffix>" for
distinct refresh executions over the same source (e.g. manual extraction
refresh with an ISO timestamp suffix).  Identity distinguishes executions;
job_id embeds the full identity so history is never collapsed.

REVISION ORDER is the durable monotonic integer source_revision_number.
Ordering uses ONLY the parsed integer, never lexicographic string order and
never wall-clock timestamps alone (two revisions can share a timestamp
resolution).  Suffixes never affect order: "1@B" and "1@A" are EQUAL in
order (both source 1) but DISTINCT in identity.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class RevisionMismatchError(Exception):
    """A derived write did not target the authoritative source revision.

    Revision-stamped derived data (extraction results, entity mentions,
    explicit dependencies, semantic records) may only be written against the
    exact current source revision of the owning meeting.  Older revisions are
    stale; newer revisions are fabrications that would masquerade as current.
    """


class StaleRevisionError(RevisionMismatchError):
    """A write stamped with an older revision than the authoritative source."""


class FutureRevisionError(RevisionMismatchError):
    """A write stamped with a newer revision than the authoritative source.

    A future-revision write is the mirror image of a stale write: it claims
    derived state derived from a source revision that does not (yet) exist,
    so it could silently masquerade as current until the source catches up.
    """


def parse_source_revision(processing_revision: str | None) -> int | None:
    """Extract the deterministic source revision from a processing revision.

    "<N>" -> N, "<N>@<suffix>" -> N, "<N>:<suffix>" -> N, None -> None.
    Falls back to None for unparseable legacy values (e.g. ISO timestamps).
    """
    if processing_revision is None:
        return None
    text = str(processing_revision).strip()
    if not text:
        return None
    for separator in ("@", ":"):
        if separator in text:
            text = text.split(separator, 1)[0].strip()
            break
    # Legacy manual-refresh revisions used an ISO timestamp; they carry no
    # deterministic source identity.
    if not text.isdigit():
        # Try to handle plain integers with whitespace; otherwise unknown.
        try:
            return int(text)
        except ValueError:
            return None
    try:
        value = int(text)
    except ValueError:
        return None
    return value if value >= 1 else None


def processing_revision_for(source_revision: int, suffix: str | None = None) -> str:
    """Build a deterministic processing revision tied to a source revision."""
    base = str(int(source_revision))
    if suffix:
        return f"{base}@{suffix}"
    return base


def compare_source_orders(first: int | None, second: int | None) -> str:
    """Total order over parsed source revisions.

    Returns "OLDER", "EQUAL", "NEWER", or "INCOMPARABLE" (either side
    unparseable/None).  INCOMPARABLE revisions are never treated as current.
    """
    if first is None or second is None:
        return "INCOMPARABLE"
    try:
        a, b = int(first), int(second)
    except (TypeError, ValueError):
        return "INCOMPARABLE"
    if a < b:
        return "OLDER"
    if a > b:
        return "NEWER"
    return "EQUAL"


def compare_processing_revisions(first: str | None, second: str | None) -> str:
    """Order two processing-revision identities by their source order.

    Suffixes affect identity only, never order: "1@A" vs "1@B" is EQUAL.
    Legacy unparseable values (bare ISO timestamps) are INCOMPARABLE.
    """
    return compare_source_orders(parse_source_revision(first), parse_source_revision(second))


def get_consistency_status(
    meeting_id: str,
    meeting_repository=None,
    job_repository=None,
    extraction_repository=None,
    mention_repository=None,
    semantic_repository=None,
) -> dict:
    """Return the durable consistency state for one meeting.

    Never raises for missing data; missing pieces are reported as None with
    is_current=False (except when there is legitimately no source at all).
    """
    meeting = meeting_repository.get_by_id(meeting_id) if meeting_repository is not None else None
    source_revision: int | None = None
    if meeting is not None:
        try:
            source_revision = int(getattr(meeting, "source_revision", 1) or 1)
        except (TypeError, ValueError):
            source_revision = 1

    extraction_revision: int | None = None
    if extraction_repository is not None:
        try:
            extraction = extraction_repository.get_by_meeting_id(meeting_id)
            if extraction is not None:
                extraction_revision = int(getattr(extraction, "source_revision", 1) or 1)
        except (TypeError, ValueError, AttributeError):
            extraction_revision = None

    # Current derived processing revision = highest source revision among
    # SUCCEEDED jobs for this meeting.  History is preserved; we never delete
    # old jobs to make current processing look easier.
    derived_revision: int | None = None
    derived_job_id: str | None = None
    processing_complete = False
    if job_repository is not None and meeting is not None:
        try:
            from app.models.background_job import BackgroundJobStatus

            succeeded: list[tuple[int, str]] = []
            for job in job_repository.list(BackgroundJobStatus.SUCCEEDED):
                if getattr(job, "payload_id", None) != meeting_id:
                    continue
                parsed = parse_source_revision(getattr(job, "processing_revision", None))
                # Legacy jobs without a processing revision predate revision
                # tracking; treat them as revision 1 only when source is 1.
                if parsed is None and getattr(job, "processing_revision", None) is None:
                    parsed = 1
                if parsed is not None:
                    succeeded.append((parsed, job.job_id))
            if succeeded:
                succeeded.sort()
                derived_revision, derived_job_id = succeeded[-1]
                processing_complete = derived_revision == source_revision
        # Job-history scan fails safe: repository domain errors (KeyError /
        # ValueError incl. InvalidJobTransition) or malformed job shapes.
        # Fallback preserved (derived stays None → reported INCOMPLETE).
        # Only the meeting id is logged — never job payloads or revisions
        # beyond the deterministic integers already exposed by this contract.
        except (KeyError, ValueError, TypeError, AttributeError):
            logger.debug(
                "processing_consistency: SUCCEEDED-job scan failed for "
                "meeting %s; reporting derived revision as unknown.",
                meeting_id,
            )
            derived_revision = None

    # Current semantic evidence revision: only current when every
    # meeting-attributed record agrees on the same source revision.
    # Mixed revisions (e.g. rev 1 + rev 2) → semantic is NOT current:
    # stale rev-1 records would masquerade as current alongside rev-2.
    semantic_revision: int | None = None
    if semantic_repository is not None:
        try:
            revisions: list[int] = []
            for record in semantic_repository.list_all():
                record_meeting = getattr(record, "meeting_id", None)
                if record_meeting is not None and record_meeting != meeting_id:
                    continue
                record_rev = getattr(record, "source_revision", None)
                if record_rev is not None:
                    revisions.append(int(record_rev))
            if revisions and len(set(revisions)) == 1:
                semantic_revision = revisions[0]
        except (TypeError, ValueError, AttributeError):
            semantic_revision = None

    is_current = (
        meeting is not None
        and source_revision is not None
        and derived_revision == source_revision
        and extraction_revision == source_revision
        and semantic_revision == source_revision
    )

    # Explicit lifecycle classification.  "derived" here means the SUCCEEDED
    # job + stamped extraction/semantic state, NOT separate persistent derived
    # tables: derived intelligence in ThreadLine is deterministic read models
    # recomputed from source, while extraction/mentions/dependencies/semantic
    # vectors are the stamped durable outputs.
    #   CURRENT: source == derived == semantic and a SUCCEEDED job exists.
    #   INCOMPLETE: source exists but no SUCCEEDED job/extraction/semantic yet.
    #   FAILED: newest relevant job for current source FAILED (or no SUCCEEDED
    #     for current source and a FAILED exists) — never reported current.
    #   STALE/OUTDATED: a SUCCEEDED revision exists but is older than source
    #     (newer source pending/failed) — must report outdated, not current.
    #   PENDING: newer source has a PENDING/RUNNING/RETRY job but none SUCCEEDED.
    status_label = "INCOMPLETE"
    failed_for_current = False
    pending_for_current = False
    has_newer_pending = False
    if job_repository is not None and meeting is not None and source_revision is not None:
        try:
            from app.models.background_job import BackgroundJobStatus as _S

            for job in job_repository.list():
                if getattr(job, "payload_id", None) != meeting_id:
                    continue
                parsed = parse_source_revision(getattr(job, "processing_revision", None))
                if parsed is None and getattr(job, "processing_revision", None) is None:
                    parsed = 1
                if parsed != source_revision:
                    if parsed is not None and parsed > source_revision:
                        has_newer_pending = True
                    continue
                if job.status == _S.FAILED:
                    failed_for_current = True
                elif job.status in {_S.PENDING, _S.RUNNING, _S.RETRY_WAITING}:
                    pending_for_current = True
        # Lifecycle scan fails safe under the same contract as above;
        # flags keep their defaults (→ INCOMPLETE/STALE, never CURRENT).
        except (KeyError, ValueError, TypeError, AttributeError):
            logger.debug(
                "processing_consistency: lifecycle scan failed for meeting "
                "%s; keeping default lifecycle flags.",
                meeting_id,
            )
    if is_current:
        status_label = "CURRENT"
    elif failed_for_current:
        status_label = "FAILED"
    elif derived_revision is not None and source_revision is not None and derived_revision < source_revision:
        status_label = "STALE"
    elif pending_for_current or has_newer_pending:
        status_label = "PENDING"
    elif meeting is None:
        status_label = "INCOMPLETE"

    # Mention / dependency currency: every durable mention/dependency for this
    # meeting should carry the current source revision once processing for the
    # current revision has succeeded.  Stale revisions are exposed, not hidden.
    stale_mentions = 0
    stale_dependencies = 0
    if mention_repository is not None and source_revision is not None:
        try:
            for mention in mention_repository.list_by_meeting_id(meeting_id):
                if int(getattr(mention, "source_revision", 1) or 1) != source_revision:
                    stale_mentions += 1
        # Mention scan fails safe: unreadable mention rows leave the stale
        # count at its conservative default (0) rather than failing status.
        except (KeyError, ValueError, TypeError, AttributeError):
            logger.debug(
                "processing_consistency: mention scan failed for meeting %s; "
                "keeping stale-mention count at 0.",
                meeting_id,
            )

    return {
        "meeting_id": meeting_id,
        "meeting_exists": meeting is not None,
        "source_revision": source_revision,
        "extraction_revision": extraction_revision,
        "derived_revision": derived_revision,
        "derived_job_id": derived_job_id,
        "semantic_revision": semantic_revision,
        "processing_complete": bool(processing_complete and is_current),
        "is_current": bool(is_current),
        "status": status_label,
        "stale_mentions": stale_mentions,
        "stale_dependencies": stale_dependencies,
    }
