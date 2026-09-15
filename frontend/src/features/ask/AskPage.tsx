import { useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { queryApi } from "../../api/intelligence";
import { useOrganisation } from "../../auth/OrganisationContext";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { userFacingMessage } from "../../types/api";
import type { EvidenceItem } from "../../types/query";
import { Alert } from "../../components/ui/Alert";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, Section } from "../../components/ui/Card";
import { Field, Input } from "../../components/ui/Input";
import { PageHeader } from "../../components/layout/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "../../components/feedback/States";
import { relativeTime, shortId } from "../dashboard/dashboardFormat";

/* Ask: the backend natural-language answer endpoint, rendered without
 * invention. The generated answer is shown separately from the exact cited
 * evidence that supports it. Remounting on organisation change guarantees a
 * previous tenant’s answer can never remain visible.
 */

function evidenceHref(kind: "entity" | "meeting", id: string): string {
  return kind === "entity"
    ? `/app/entities/${encodeURIComponent(id)}`
    : `/app/meetings/${encodeURIComponent(id)}`;
}

function EvidenceLinks({ item }: { item: EvidenceItem }): React.JSX.Element | null {
  if (!item.entity_id && !item.meeting_id) return null;
  return (
    <p className="tl-body-secondary">
      {item.entity_id ? (
        <Link
          className="tl-link-strong"
          aria-label={`Open linked entity ${shortId(item.entity_id)}`}
          to={evidenceHref("entity", item.entity_id)}
        >
          Open linked entity
        </Link>
      ) : null}
      {item.entity_id && item.meeting_id ? " · " : null}
      {item.meeting_id ? (
        <Link
          className="tl-link-strong"
          aria-label={`Open source meeting ${shortId(item.meeting_id)}`}
          to={evidenceHref("meeting", item.meeting_id)}
        >
          Open source meeting
        </Link>
      ) : null}
    </p>
  );
}

function EvidenceTimestamp({ value }: { value: string | null }): React.JSX.Element {
  const text = relativeTime(value) ?? value ?? "Date unavailable";
  return value ? (
    <time dateTime={value} title={value}>
      {text}
    </time>
  ) : (
    <span>{text}</span>
  );
}

function AskWorkspace(): React.JSX.Element {
  const [question, setQuestion] = useState("");
  const [error, setError] = useState<string | null>(null);

  const ask = useMutation({
    mutationFn: (text: string) =>
      queryApi.ask({ question: text, max_evidence_items: 8, include_source_text: true }),
    onError: (failure) => setError(userFacingMessage(failure)),
  });

  const citedEvidence = useMemo(() => {
    if (!ask.data) return [];
    const cited = new Set(ask.data.cited_evidence_ids);
    return ask.data.evidence.filter((item) => cited.has(item.evidence_id));
  }, [ask.data]);

  function submitQuestion(text: string): void {
    const trimmed = text.trim();
    if (!trimmed || ask.isPending) return;
    setError(null);
    // Clear any previous tenant/question result before the new backend answer
    // arrives; the pending state must never show a stale answer as current.
    ask.reset();
    ask.mutate(trimmed);
  }

  function onSubmit(event: FormEvent): void {
    event.preventDefault();
    submitQuestion(question);
  }

  function onRetry(): void {
    const retryQuestion = ask.variables ?? question.trim();
    if (retryQuestion) submitQuestion(retryQuestion);
  }

  return (
    <div className="tl-page">
      <PageHeader
        title="Ask ThreadLine"
        description="Ask about your organisation in plain language. ThreadLine calls the backend answer service and shows the generated answer separately from its cited meeting evidence — scoped to this organisation only."
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
            Ask ThreadLine
          </Button>
        </form>
      </Card>

      {error ? (
        <Alert tone="danger" title="Couldn't ask">
          {error}
        </Alert>
      ) : null}

      {ask.isPending ? <LoadingState title="Asking ThreadLine" /> : null}
      {ask.isError && !ask.isPending ? (
        <ErrorState error={ask.error} onRetry={onRetry} />
      ) : null}

      {ask.isSuccess && !ask.isPending && ask.variables ? (
        <>
          <p className="tl-body-secondary">Question: {ask.variables}</p>
          {ask.data.warnings.length > 0 ? (
            <Alert tone="warning" title="Answer caveats">
              <ul>
                {ask.data.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </Alert>
          ) : null}
          {ask.data.insufficient_evidence ? (
            <EmptyState
              title="No grounded answer"
              body={ask.data.answer}
              icon={<Search aria-hidden="true" />}
            />
          ) : (
            <Section
              title="Answer"
              description={`Intent ${ask.data.intent} · Generated ${
                relativeTime(ask.data.generated_at) ?? ask.data.generated_at ?? "at an unknown time"
              }`}
            >
              <Card>
                <p>{ask.data.answer}</p>
              </Card>
            </Section>
          )}
          {!ask.data.insufficient_evidence && citedEvidence.length > 0 ? (
            <Section
              title="Cited evidence"
              description={`${citedEvidence.length} cited ${citedEvidence.length === 1 ? "item" : "items"} · intent ${ask.data.intent}`}
            >
              <ul className="tl-evidence-list">
                {citedEvidence.map((item) => (
                  <li key={item.evidence_id}>
                    <Card>
                      <div className="tl-evidence-head">
                        <Badge tone="teal">{item.evidence_type}</Badge>
                        <span className="tl-body-secondary">
                          <EvidenceTimestamp value={item.timestamp} />
                        </span>
                      </div>
                      <p className="tl-evidence-summary">{item.summary}</p>
                      {item.source_text ? (
                        <blockquote className="tl-evidence-quote">{item.source_text}</blockquote>
                      ) : null}
                      {item.source_reference ? (
                        <p className="tl-body-secondary">Source reference: {item.source_reference}</p>
                      ) : null}
                      <EvidenceLinks item={item} />
                    </Card>
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}
          {!ask.data.insufficient_evidence && citedEvidence.length === 0 ? (
            <EmptyState
              title="No cited evidence"
              body="The backend returned an answer without cited evidence identifiers."
              icon={<Search aria-hidden="true" />}
            />
          ) : null}
        </>
      ) : null}
    </div>
  );
}

export function AskPage(): React.JSX.Element {
  useDocumentTitle("Ask");
  const { organisationId } = useOrganisation();
  return <AskWorkspace key={organisationId ?? "signed-out"} />;
}
