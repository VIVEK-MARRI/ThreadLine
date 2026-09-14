import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { queryApi } from "../../api/intelligence";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { userFacingMessage } from "../../types/api";
import { Alert } from "../../components/ui/Alert";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, Section } from "../../components/ui/Card";
import { Field, Input } from "../../components/ui/Input";
import { PageHeader } from "../../components/layout/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "../../components/feedback/States";

/* Ask (foundation): evidence-backed answers are one mutation away.
 * This screen shows the exact evidence ThreadLine would cite — the same
 * items passed to the answer provider — with links back to meetings. */

export function AskPage(): React.JSX.Element {
  useDocumentTitle("Ask");
  const [question, setQuestion] = useState("");
  const [error, setError] = useState<string | null>(null);

  const ask = useMutation({
    mutationFn: (text: string) =>
      queryApi.evidence({ question: text, max_evidence_items: 8, include_source_text: true }),
    onError: (failure) => setError(userFacingMessage(failure)),
  });

  function onSubmit(event: FormEvent): void {
    event.preventDefault();
    const text = question.trim();
    if (!text || ask.isPending) return;
    setError(null);
    ask.mutate(text);
  }

  return (
    <div className="tl-page">
      <PageHeader
        title="Ask ThreadLine"
        description="Ask about your organisation in plain language. Answers are grounded in cited meeting evidence — scoped to this organisation only."
      />
      <Card>
        <form onSubmit={onSubmit} className="tl-ask-form">
          <Field label="Your question" htmlFor="ask-question" required>
            <Input
              id="ask-question"
              required
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="What is blocking the payment API?"
              autoComplete="off"
            />
          </Field>
          <Button type="submit" variant="primary" loading={ask.isPending}>
            Search evidence
          </Button>
        </form>
      </Card>

      {error ? (
        <Alert tone="danger" title="Couldn't search">
          {error}
        </Alert>
      ) : null}

      {ask.isPending ? <LoadingState title="Searching your meetings" /> : null}
      {ask.isError ? (
        <ErrorState error={ask.error} onRetry={() => ask.mutate(question.trim())} />
      ) : null}

      {ask.isSuccess && ask.data.evidence.length === 0 ? (
        <EmptyState
          title="No evidence found"
          body="Nothing in your processed meetings speaks to that question yet. Try different wording, or ingest the meeting where it was discussed."
          icon={<Search aria-hidden="true" />}
        />
      ) : null}

      {ask.isSuccess && ask.data.evidence.length > 0 ? (
        <Section
          title="Evidence"
          description={`${ask.data.evidence.length} item${ask.data.evidence.length === 1 ? "" : "s"} · intent ${ask.data.intent}`}
        >
          <ul className="tl-evidence-list">
            {ask.data.evidence.map((item) => (
              <li key={item.evidence_id}>
                <Card>
                  <div className="tl-evidence-head">
                    <Badge tone="teal">{item.evidence_type}</Badge>
                    {item.meeting_id ? (
                      <Link
                        className="tl-link-strong"
                        to={`/app/meetings/${encodeURIComponent(item.meeting_id)}`}
                      >
                        {item.meeting_id}
                      </Link>
                    ) : null}
                  </div>
                  <p className="tl-evidence-summary">{item.summary}</p>
                  {item.source_text ? (
                    <blockquote className="tl-evidence-quote">{item.source_text}</blockquote>
                  ) : null}
                </Card>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}
