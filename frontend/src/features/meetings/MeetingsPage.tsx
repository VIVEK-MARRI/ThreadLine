import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FilePlus2 } from "lucide-react";
import { meetingsApi } from "../../api/meetings";
import { useOrganisation } from "../../auth/OrganisationContext";
import { canCreateMeeting } from "../../auth/permissions";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { userFacingMessage } from "../../types/api";
import type { MeetingSummary } from "../../types/meetings";
import { Alert } from "../../components/ui/Alert";
import { Button } from "../../components/ui/Button";
import { StatusBadge } from "../../components/ui/Badge";
import { Card, Section } from "../../components/ui/Card";
import { Field, Input, Select, Textarea } from "../../components/ui/Input";
import { Table } from "../../components/ui/Table";
import { PageHeader } from "../../components/layout/PageHeader";
import { EmptyState, ErrorState, Skeleton } from "../../components/feedback/States";
import { useToast } from "../../components/ui/Toast";
import { useMeetingList } from "./useMeetings";
import {
  DEFAULT_MEETING_LIST_LIMIT,
  MEETING_LIST_LIMIT_OPTIONS,
  applyLoadedMeetingFilters,
  formatMeetingDateTime,
  hasActiveLoadedMeetingFilters,
  linkedEntitySummary,
  listFactSummary,
  parseMeetingListLimit,
  parseMeetingSort,
  parseMeetingStatusFilter,
  participantPreview,
  processingStatusLabel,
  processingStatusTone,
  type MeetingListSort,
  type MeetingListStatusFilter,
} from "./meetingsFormat";
import "./meetings.css";

/* Meetings workspace list: tenant-scoped server list plus transparent
 * filtering over the loaded page. Search, status, date, sort, and page size
 * stay in the URL; only the backend-supported page size changes requests.
 * The URL is the single source of truth for filters, so clearing, sorting,
 * and back/forward navigation can never disagree with the input controls.
 */

function parseParticipants(value: string): string[] {
  return value
    .split(",")
    .map((name) => name.trim())
    .filter(Boolean);
}

export function MeetingsPage(): React.JSX.Element {
  useDocumentTitle("Meetings");
  const navigate = useNavigate();
  const { toast } = useToast();
  const { organisationId, role } = useOrganisation();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();

  const limit = parseMeetingListLimit(params.get("limit"));
  const status = parseMeetingStatusFilter(params.get("status"));
  const sort = parseMeetingSort(params.get("sort"));
  const fromDate = params.get("from") ?? "";
  const toDate = params.get("to") ?? "";
  const urlQuery = params.get("q") ?? "";

  const list = useMeetingList(limit);
  const meetings = useMemo(() => list.data?.meetings ?? [], [list.data]);
  const filters = useMemo(
    () => ({ query: urlQuery, status, fromDate, toDate, sort }),
    [urlQuery, status, fromDate, toDate, sort],
  );
  const visible = useMemo(() => applyLoadedMeetingFilters(meetings, filters), [meetings, filters]);
  const filtersActive = hasActiveLoadedMeetingFilters(filters);

  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [transcript, setTranscript] = useState("");
  const [meetingDate, setMeetingDate] = useState(() => new Date().toISOString().slice(0, 16));
  const [participants, setParticipants] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const ingest = useMutation({
    mutationFn: () =>
      meetingsApi.ingest({
        title: title.trim(),
        transcript: transcript.trim(),
        meeting_date: new Date(meetingDate).toISOString(),
        participants: parseParticipants(participants),
      }),
    onSuccess: (response) => {
      if (organisationId) {
        void queryClient.invalidateQueries({ queryKey: ["tl", organisationId, "meetings"] });
      }
      toast({ title: "Meeting ingested", body: "Processing started in the background." });
      setTitle("");
      setTranscript("");
      setParticipants("");
      setFormError(null);
      setShowForm(false);
      navigate(`/app/meetings/${encodeURIComponent(response.meeting_id)}`);
    },
    onError: (failure) => setFormError(userFacingMessage(failure)),
  });

  function updateParam(name: string, value: string): void {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next, { replace: true });
  }

  function onSubmit(event: FormEvent): void {
    event.preventDefault();
    if (!title.trim()) {
      setFormError("Add a meeting title before ingesting.");
      return;
    }
    if (!transcript.trim()) {
      setFormError("Add the meeting transcript before ingesting.");
      return;
    }
    if (Number.isNaN(new Date(meetingDate).getTime())) {
      setFormError("Add a valid meeting date and time before ingesting.");
      return;
    }
    setFormError(null);
    ingest.mutate();
  }

  function cancelForm(): void {
    setShowForm(false);
    setFormError(null);
  }

  function renderRows(): React.JSX.Element {
    if (list.isLoading) {
      return (
        <div className="tl-meeting-loading-block">
          <Skeleton lines={6} label="Loading meetings" />
        </div>
      );
    }
    if (list.isError) {
      return <ErrorState title="Couldn't load meetings" error={list.error} onRetry={() => void list.refetch()} />;
    }
    if (meetings.length === 0) {
      return (
        <EmptyState
          title="Your meeting memory starts here."
          body="Ingest a transcript and ThreadLine will preserve the source, process it, and connect the facts it contains."
          icon={<FilePlus2 aria-hidden="true" />}
          action={
            canCreateMeeting(role) ? (
              <Button variant="primary" onClick={() => setShowForm(true)}>
                Create meeting
              </Button>
            ) : undefined
          }
        />
      );
    }
    return (
      <>
        <p className="tl-meeting-result-count" role="status">
          Showing {visible.length} of {meetings.length} loaded{" "}
          {meetings.length === 1 ? "meeting" : "meetings"}
          {list.data?.has_more ? " · more meetings exist beyond this loaded set" : ""}.
          Search and filters apply to these loaded meetings.
        </p>
        <Table<MeetingSummary>
          caption={`Meetings, ${sort === "oldest" ? "oldest first" : "newest first"}`}
          columns={[
            {
              key: "meeting",
              header: "Meeting",
              render: (meeting) => (
                <span>
                  <Link
                    className="tl-meeting-row-title"
                    to={`/app/meetings/${encodeURIComponent(meeting.meeting_id)}`}
                  >
                    {meeting.title}
                  </Link>
                  <span className="tl-meeting-row-meta">
                    {formatMeetingDateTime(meeting.meeting_date) ?? "Date unknown"}
                    {meeting.participants.length > 0
                      ? ` · ${participantPreview(meeting.participants).shown.join(", ")}${
                          participantPreview(meeting.participants).remaining > 0
                            ? ` +${participantPreview(meeting.participants).remaining}`
                            : ""
                        }`
                      : " · No participants listed"}
                  </span>
                </span>
              ),
            },
            {
              key: "processing",
              header: "Processing",
              render: (meeting) => (
                <StatusBadge
                  tone={processingStatusTone(meeting.processing_status)}
                  label={processingStatusLabel(meeting.processing_status)}
                  pulse={meeting.processing_status === "PENDING"}
                />
              ),
            },
            {
              key: "facts",
              header: "Extracted facts",
              render: (meeting) => (
                <span className="tl-meeting-cell-muted">{listFactSummary(meeting)}</span>
              ),
            },
            {
              key: "entities",
              header: "Linked entities",
              render: (meeting) => (
                <span className="tl-meeting-cell-muted">
                  {linkedEntitySummary(meeting.resolved_entity_count)}
                </span>
              ),
            },
          ]}
          rows={visible}
          keyOf={(meeting) => meeting.meeting_id}
          empty={
            <EmptyState
              title="No loaded meetings match these filters."
              body="Clear the search or filters to see the loaded meeting set again."
              action={
                <Button variant="secondary" onClick={() => setParams({}, { replace: true })}>
                  Clear search and filters
                </Button>
              }
            />
          }
        />
      </>
    );
  }

  return (
    <div className="tl-page">
      <PageHeader
        title="Meetings"
        description="Your organisation's meeting memory: source transcripts, processing state, extracted facts, and linked entities."
        crumbs={[{ label: "Meetings" }]}
        actions={
          canCreateMeeting(role) ? (
            <Button
              variant={showForm ? "secondary" : "primary"}
              aria-expanded={showForm}
              onClick={() => (showForm ? cancelForm() : setShowForm(true))}
            >
              {showForm ? "Close new meeting" : "New meeting"}
            </Button>
          ) : undefined
        }
      />

      {showForm && canCreateMeeting(role) ? (
        <div className="tl-meeting-create">
          <Card>
            <form onSubmit={onSubmit} className="tl-form-stack" aria-label="New meeting">
              <Field label="Title" htmlFor="meeting-title" required>
                <Input
                  id="meeting-title"
                  required
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="Weekly delivery sync"
                />
              </Field>
              <Field label="Transcript" htmlFor="meeting-transcript" required>
                <Textarea
                  id="meeting-transcript"
                  required
                  rows={8}
                  value={transcript}
                  onChange={(event) => setTranscript(event.target.value)}
                  placeholder="Paste the full meeting transcript here…"
                />
              </Field>
              <div className="tl-form-row">
                <Field label="Meeting date" htmlFor="meeting-date" required>
                  <Input
                    id="meeting-date"
                    type="datetime-local"
                    required
                    value={meetingDate}
                    onChange={(event) => setMeetingDate(event.target.value)}
                  />
                </Field>
                <Field
                  label="Participants"
                  htmlFor="meeting-participants"
                  hint="Comma-separated names."
                >
                  <Input
                    id="meeting-participants"
                    value={participants}
                    onChange={(event) => setParticipants(event.target.value)}
                    placeholder="Rahul Kumar, Priya Nair"
                  />
                </Field>
              </div>
              {formError ? (
                <Alert tone="danger" title="Couldn't ingest the meeting">
                  {formError}
                </Alert>
              ) : null}
              <div className="tl-meeting-toolbar-actions">
                <Button type="submit" variant="primary" loading={ingest.isPending}>
                  Create meeting
                </Button>
                <Button type="button" variant="secondary" onClick={cancelForm}>
                  Cancel
                </Button>
              </div>
            </form>
          </Card>
        </div>
      ) : null}

      <Section
        title="Recent meetings"
        description={`Newest first. The server returns up to ${limit} meetings; search and filters apply to that loaded set.`}
      >
        <form
          role="search"
          aria-label="Find loaded meetings"
          className="tl-meeting-toolbar"
          onSubmit={(event) => event.preventDefault()}
        >
          <Field
            label="Search loaded meetings"
            htmlFor="meeting-search"
            hint="Matches titles, participant names, and meeting IDs in the loaded set."
          >
            <Input
              id="meeting-search"
              type="search"
              value={urlQuery}
              onChange={(event) => updateParam("q", event.target.value)}
              placeholder="Search title, participant, or ID"
            />
          </Field>
          <Field label="Processing" htmlFor="meeting-status">
            <Select
              id="meeting-status"
              value={status}
              onChange={(event) =>
                updateParam("status", event.target.value === "ALL" ? "" : event.target.value)
              }
            >
              <option value="ALL">All processing states</option>
              <option value="CURRENT">Complete</option>
              <option value="PENDING">Processing</option>
              <option value="INCOMPLETE">Awaiting processing</option>
              <option value="FAILED">Failed</option>
              <option value="STALE">Needs refresh</option>
            </Select>
          </Field>
          <Field label="From" htmlFor="meeting-from">
            <Input
              id="meeting-from"
              type="date"
              value={fromDate}
              onChange={(event) => updateParam("from", event.target.value)}
            />
          </Field>
          <Field label="To" htmlFor="meeting-to">
            <Input
              id="meeting-to"
              type="date"
              value={toDate}
              onChange={(event) => updateParam("to", event.target.value)}
            />
          </Field>
          <div className="tl-meeting-toolbar-actions">
            <Field label="Sort" htmlFor="meeting-sort">
              <Select
                id="meeting-sort"
                value={sort}
                onChange={(event) =>
                  updateParam("sort", event.target.value === "newest" ? "" : event.target.value)
                }
              >
                <option value="newest">Newest first</option>
                <option value="oldest">Oldest first</option>
              </Select>
            </Field>
            <Field label="Page size" htmlFor="meeting-limit">
              <Select
                id="meeting-limit"
                value={String(limit)}
                onChange={(event) =>
                  updateParam(
                    "limit",
                    event.target.value === String(DEFAULT_MEETING_LIST_LIMIT) ? "" : event.target.value,
                  )
                }
              >
                {MEETING_LIST_LIMIT_OPTIONS.map((option) => (
                  <option key={option} value={String(option)}>
                    {option}
                  </option>
                ))}
              </Select>
            </Field>
            <Button
              type="button"
              variant="secondary"
              disabled={!filtersActive}
              onClick={() => setParams({}, { replace: true })}
            >
              Clear
            </Button>
          </div>
        </form>
        {renderRows()}
      </Section>
    </div>
  );
}

export type { MeetingListSort, MeetingListStatusFilter };
