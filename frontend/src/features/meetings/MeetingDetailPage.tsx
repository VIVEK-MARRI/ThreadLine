import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { meetingsApi } from "../../api/meetings";
import { queryKeys } from "../../api/keys";
import { useOrganisation } from "../../auth/OrganisationContext";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { userFacingMessage } from "../../types/api";
import { Alert } from "../../components/ui/Alert";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, Section } from "../../components/ui/Card";
import { PageHeader } from "../../components/layout/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "../../components/feedback/States";
import { useToast } from "../../components/ui/Toast";
import { useState } from "react";

function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function MeetingDetailPage(): React.JSX.Element {
  const { meetingId = "" } = useParams();
  useDocumentTitle("Meeting");
  const { organisationId } = useOrganisation();
  const { toast } = useToast();
  const [notice, setNotice] = useState<string | null>(null);

  const detail = useQuery({
    queryKey: queryKeys.meeting(organisationId ?? "", meetingId),
    queryFn: () => meetingsApi.get(meetingId),
    enabled: Boolean(organisationId && meetingId),
  });

  const extract = useMutation({
    mutationFn: () => meetingsApi.extract(meetingId),
    onSuccess: () => {
      setNotice("Reprocessing started. Fresh facts will appear as the worker finishes.");
      toast({ title: "Extraction refresh queued" });
    },
    onError: (failure) => setNotice(userFacingMessage(failure)),
  });

  if (detail.isPending) return <LoadingState title="Loading meeting" />;
  if (detail.isError) {
    return (
      <div className="tl-page">
        <PageHeader title="Meeting" crumbs={[{ label: "Meetings", to: "/app/meetings" }]} />
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      </div>
    );
  }

  const meeting = detail.data;

  return (
    <div className="tl-page">
      <PageHeader
        title={meeting.title}
        description={`Ingested ${formatDateTime(meeting.ingested_at)} · ${meeting.meeting_id}`}
        crumbs={[{ label: "Meetings", to: "/app/meetings" }, { label: meeting.title }]}
        actions={
          <Button variant="secondary" loading={extract.isPending} onClick={() => extract.mutate()}>
            Refresh extraction
          </Button>
        }
      />
      {notice ? (
        <Alert tone="info" title="Processing">
          {notice}
        </Alert>
      ) : null}
      <div className="tl-split">
        <Section title="Transcript">
          <Card>
            <p className="tl-transcript">{meeting.transcript}</p>
          </Card>
        </Section>
        <Section title="Details">
          <Card>
            <dl className="tl-facts">
              <div>
                <dt>Meeting date</dt>
                <dd className="tl-numeric">{formatDateTime(meeting.meeting_date)}</dd>
              </div>
              <div>
                <dt>Meeting ID</dt>
                <dd className="tl-mono">{meeting.meeting_id}</dd>
              </div>
              <div>
                <dt>Participants</dt>
                <dd>
                  {meeting.participants.length === 0 ? (
                    <EmptyState title="No participants listed" body="This transcript names no participants." />
                  ) : (
                    <span className="tl-badge-row">
                      {meeting.participants.map((name) => (
                        <Badge key={name}>{name}</Badge>
                      ))}
                    </span>
                  )}
                </dd>
              </div>
            </dl>
          </Card>
        </Section>
      </div>
    </div>
  );
}
