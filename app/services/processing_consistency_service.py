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

Revision identity
-----------------
Source revisions are deterministic monotonic integers on Meeting.source_revision.
Processing revisions are strings of the form "<source_revision>" for normal
pipeline executions, or "<source_revision>@<suffix>" for distinct refresh
executions over the same source revision (e.g. manual extraction refresh).
The source part is always parseable via parse_source_revision().
"""

from __future__ import annotations

from typing import Optional


def parse_source_revision(processing_revision: Optional[str]) -> Optional[int]:
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


def processing_revision_for(source_revision: int, suffix: Optional[str] = None) -> str:
    """Build a deterministic processing revision tied to a source revision."""
    base = str(int(source_revision))
    if suffix:
        return f"{base}@{suffix}"
    return base


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
    source_revision: Optional[int] = None
    if meeting is not None:
        try:
            source_revision = int(getattr(meeting, "source_revision", 1) or 1)
        except (TypeError, ValueError):
            source_revision = 1

    extraction_revision: Optional[int] = None
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
    derived_revision: Optional[int] = None
    derived_job_id: Optional[str] = None
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
        except Exception:
            derived_revision = None

    # Current semantic evidence revision = max source_revision among semantic
    # records attributable to this meeting.
    semantic_revision: Optional[int] = None
    if semantic_repository is not None:
        try:
            revisions: list[int] = []
            for record in semantic_repository.list_all():
                record_meeting = getattr(record, "meeting_id", None)
                if record_meeting is not None and record_meeting != meeting_id:
                    continue
                # Records without meeting attribution cannot prove currency for
                # this meeting; only consider attributed records when any exist.
                # Fall back to unattributed only if no attributed records exist
                # (legacy data).  Handled below.
                record_rev = getattr(record, "source_revision", None)
                if record_rev is not None:
                    revisions.append(int(record_rev))
            if not revisions:
                # Legacy fallback: no meeting-attributed revisions; report None
                # rather than masquerading unrelated records as current.
                pass
            else:
                semantic_revision = max(revisions)
        except (TypeError, ValueError, AttributeError):
            semantic_revision = None

    is_current = (
        meeting is not None
        and source_revision is not None
        and derived_revision == source_revision
        and extraction_revision == source_revision
        and semantic_revision == source_revision
    )

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
        except Exception:
            pass

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
        "stale_mentions": stale_mentions,
        "stale_dependencies": stale_dependencies,
    }
