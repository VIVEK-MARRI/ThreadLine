import { Link, useParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { meetingsApi } from "../../api/meetings";
import { useOrganisation } from "../../auth/OrganisationContext";
import { canRunMeetingProcessing } from "../../auth/permissions";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { ApiError, userFacingMessage } from "../../types/api";
import { Alert } from "../../components/ui/Alert";
import { Button } from "../../components/ui/Button";
import { PageHeader } from "../../components/layout/PageHeader";
import { EmptyState, ErrorState, Skeleton } from "../../components/feedback/States";
import { useToast } from "../../components/ui/Toast";
import { useMeetingDetail, useMeetingProcessing } from "./useMeetings";
import {
  MeetingChangesSection,
  MeetingDependenciesSection,
  MeetingEntitiesSection,
  MeetingFactsSection,
  MeetingMetadataRail,
  MeetingProcessingSection,
  MeetingTranscriptSection,
} from "./MeetingDetailSections";
import { formatMeetingDateTime } from "./meetingsFormat";
import "./meetings.css";

/* Meeting detail: one meeting as a node in organisational memory. Sections
 * stay independent; extraction refresh is explicit and never used to read
 * stored facts.
 */

export function MeetingDetailPage(): React.JSX.Element {
  const { meetingId = "" } = useParams();
  const { organisationId, role } = useOrganisation();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);

  const detail = useMeetingDetail(meetingId);
  useDocumentTitle(detail.data?.title ?? "Meeting");
  const processing = useMeetingProcessing(meetingId);

  const extract = useMutation({
    mutationFn: () => meetingsApi.extract(meetingId),
    onSuccess: () => {
      if (organisationId) {
        void queryClient.invalidateQueries({ queryKey: ["tl", organisationId, "meeting", meetingId] });
        void queryClient.invalidateQueries({ queryKey: ["tl", organisationId, "meetings"] });
      }
      setNotice("Reprocessing started. Fresh facts will appear as the worker finishes.");
      toast({ title: "Extraction refresh queued" });
    },
    onError: (failure) => setNotice(userFacingMessage(failure)),
  });

  if (detail.isLoading) {
    return (
      <div className="tl-page">
        <PageHeader title="Meeting" description="Loading meeting memory…" />
        <div className="tl-meeting-loading-block">
          <Skeleton lines={3} label="Loading meeting title and metadata" />
        </div>
        <div className="tl-meeting-layout">
          <div className="tl-meeting-main">
            <Skeleton lines={5} label="Loading meeting facts" />
            <Skeleton lines={4} label="Loading linked entities" />
            <Skeleton lines={8} label="Loading transcript" />
          </div>
          <div className="tl-meeting-rail">
            <Skeleton lines={5} label="Loading meeting details" />
          </div>
        </div>
      </div>
    );
  }

  if (detail.isError) {
    const unavailable =
      detail.error instanceof ApiError &&
      (detail.error.kind === "not-found" || detail.error.kind === "forbidden");
    return (
      <div className="tl-page">
        <PageHeader
          title="Meeting unavailable"
          crumbs={[{ label: "Meetings", to: "/app/meetings" }]}
        />
        {unavailable ? (
          <EmptyState
            title="This meeting isn't available"
            body="It may not exist, or it may belong to another organisation."
            action={
              <Link className="tl-btn tl-btn-secondary" to="/app/meetings">
                Back to meetings
              </Link>
            }
          />
        ) : (
          <ErrorState
            title="Couldn't load this meeting"
            error={detail.error}
            onRetry={() => void detail.refetch()}
          />
        )}
      </div>
    );
  }

  const meeting = detail.data;
  if (!meeting) {
    return (
      <div className="tl-page">
        <PageHeader
          title="Meeting unavailable"
          crumbs={[{ label: "Meetings", to: "/app/meetings" }]}
        />
        <ErrorState
          title="Couldn't load this meeting"
          error={new Error("The meeting response was empty.")}
          onRetry={() => void detail.refetch()}
        />
      </div>
    );
  }
  const meetingDate = formatMeetingDateTime(meeting.meeting_date) ?? "Date unknown";

  return (
    <div className="tl-page">
      <PageHeader
        title={meeting.title}
        description={`${meetingDate} · Source revision ${processing.data?.source_revision ?? "unknown"}`}
        crumbs={[{ label: "Meetings", to: "/app/meetings" }, { label: meeting.title }]}
        actions={
          canRunMeetingProcessing(role) ? (
            <Button variant="secondary" loading={extract.isPending} onClick={() => extract.mutate()}>
              Refresh extraction
            </Button>
          ) : undefined
        }
      />
      {notice ? (
        <Alert tone="info" title="Processing">
          {notice}
        </Alert>
      ) : null}
      {processing.data?.status === "FAILED" ? (
        <Alert tone="danger" title="Processing failed for the current source">
          The stored facts may be outdated. Refresh extraction to queue a new processing revision.
        </Alert>
      ) : null}
      {processing.data?.status === "STALE" ? (
        <Alert tone="warning" title="Newer source is waiting for processing">
          This meeting was revised after its last successful run. Refresh extraction to process the
          current source revision.
        </Alert>
      ) : null}
      {processing.data?.status === "PENDING" ? (
        <Alert tone="info" title="Processing is running">
          {processing.data.worker_enabled
            ? "The worker is processing this meeting. This status refreshes automatically."
            : "The background worker is off, so queued work is waiting."}
        </Alert>
      ) : null}

      <div className="tl-meeting-layout">
        <div className="tl-meeting-main">
          <MeetingProcessingSection meetingId={meetingId} />
          <MeetingFactsSection meetingId={meetingId} />
          <MeetingEntitiesSection meetingId={meetingId} />
          <MeetingDependenciesSection meetingId={meetingId} />
          <MeetingChangesSection meetingId={meetingId} />
          <MeetingTranscriptSection transcript={meeting.transcript} />
        </div>
        <MeetingMetadataRail meeting={meeting} processing={processing.data} />
      </div>
    </div>
  );
}
