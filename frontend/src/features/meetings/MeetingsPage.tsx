import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { FilePlus2 } from "lucide-react";
import { meetingsApi } from "../../api/meetings";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { userFacingMessage } from "../../types/api";
import { Alert } from "../../components/ui/Alert";
import { Button } from "../../components/ui/Button";
import { Card, Section } from "../../components/ui/Card";
import { Field, Input, Textarea } from "../../components/ui/Input";
import { PageHeader } from "../../components/layout/PageHeader";
import { EmptyState } from "../../components/feedback/States";
import { useToast } from "../../components/ui/Toast";

interface SessionIngest {
  meeting_id: string;
  title: string;
}

/* Meetings: ingest transcripts (real POST) + this-session history.
 * The backend has no list endpoint, so history here is honestly labelled
 * as this browser session's ingestions — never invented records. */

export function MeetingsPage(): React.JSX.Element {
  useDocumentTitle("Meetings");
  const navigate = useNavigate();
  const { toast } = useToast();
  const [session, setSession] = useState<SessionIngest[]>([]);

  const [title, setTitle] = useState("");
  const [transcript, setTranscript] = useState("");
  const [meetingDate, setMeetingDate] = useState(() => new Date().toISOString().slice(0, 16));
  const [participants, setParticipants] = useState("");
  const [error, setError] = useState<string | null>(null);

  const ingest = useMutation({
    mutationFn: () =>
      meetingsApi.ingest({
        title: title.trim(),
        transcript: transcript.trim(),
        meeting_date: new Date(meetingDate).toISOString(),
        participants: participants
          .split(",")
          .map((name) => name.trim())
          .filter(Boolean),
      }),
    onSuccess: (response) => {
      setSession((current) => [
        { meeting_id: response.meeting_id, title: title.trim() },
        ...current,
      ]);
      toast({ title: "Meeting ingested", body: "Processing started in the background." });
      navigate(`/app/meetings/${encodeURIComponent(response.meeting_id)}`);
    },
    onError: (failure) => setError(userFacingMessage(failure)),
  });

  function onSubmit(event: FormEvent): void {
    event.preventDefault();
    setError(null);
    ingest.mutate();
  }

  return (
    <div className="tl-page">
      <PageHeader
        title="Meetings"
        description="Ingest a transcript to create durable, revisioned source truth. Background processing extracts people, issues, and commitments."
      />
      {error ? (
        <Alert tone="danger" title="Couldn't ingest the meeting">
          {error}
        </Alert>
      ) : null}
      <div className="tl-split">
        <Card>
          <form onSubmit={onSubmit} className="tl-form-stack">
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
            <div>
              <Button type="submit" variant="primary" loading={ingest.isPending}>
                Ingest meeting
              </Button>
            </div>
          </form>
        </Card>
        <Section title="Ingested this session" description="Meetings you added in this browser session.">
          {session.length === 0 ? (
            <EmptyState
              title="Nothing ingested yet"
              body="Meetings you ingest will appear here so you can jump back to them."
              icon={<FilePlus2 aria-hidden="true" />}
            />
          ) : (
            <ul className="tl-session-list">
              {session.map((item) => (
                <li key={item.meeting_id}>
                  <Link to={`/app/meetings/${encodeURIComponent(item.meeting_id)}`}>
                    {item.title}
                  </Link>
                  <span className="tl-session-id">{item.meeting_id}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>
    </div>
  );
}
