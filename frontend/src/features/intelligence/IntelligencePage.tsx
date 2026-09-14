import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { intelligenceApi } from "../../api/intelligence";
import { queryKeys } from "../../api/keys";
import { useOrganisation } from "../../auth/OrganisationContext";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { Badge } from "../../components/ui/Badge";
import { Card, Section } from "../../components/ui/Card";
import { PageHeader } from "../../components/layout/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "../../components/feedback/States";
import { StatusBadge } from "../../components/ui/Badge";
import { attentionLevelTone } from "../../components/ui/status";

/* Intelligence (foundation): real counts + real top items from the three
 * organisation signals. Deep dives arrive in the next stage. */

export function IntelligencePage(): React.JSX.Element {
  useDocumentTitle("Intelligence");
  const { organisationId } = useOrganisation();

  const attention = useQuery({
    queryKey: queryKeys.attention(organisationId ?? ""),
    queryFn: () => intelligenceApi.attention(),
    enabled: Boolean(organisationId),
  });
  const portfolio = useQuery({
    queryKey: queryKeys.portfolio(organisationId ?? ""),
    queryFn: () => intelligenceApi.portfolio(),
    enabled: Boolean(organisationId),
  });
  const changes = useQuery({
    queryKey: queryKeys.changes(organisationId ?? "", { limit: 8 }),
    queryFn: () => intelligenceApi.changes({ limit: 8 }),
    enabled: Boolean(organisationId),
  });

  return (
    <div className="tl-page">
      <PageHeader
        title="Intelligence"
        description="What needs your attention across the organisation — computed deterministically from your meetings, never guessed."
      />
      <div className="tl-grid-3">
        <Card>
          <h2 className="tl-section-title">Attention</h2>
          {attention.isPending ? (
            <LoadingState title="Loading attention" />
          ) : attention.isError ? (
            <ErrorState error={attention.error} onRetry={() => void attention.refetch()} />
          ) : attention.data.items.length === 0 ? (
            <EmptyState title="All quiet" body="No entity currently needs attention." />
          ) : (
            <ul className="tl-top-list">
              {attention.data.items.slice(0, 5).map((item) => (
                <li key={item.attention_id}>
                  <StatusBadge tone={attentionLevelTone(item.attention_level)} label={item.attention_level} />
                  <Link
                    className="tl-link-strong"
                    to={`/app/entities/${encodeURIComponent(item.entity_id)}`}
                  >
                    {item.entity_id}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card>
          <h2 className="tl-section-title">Risk posture</h2>
          {portfolio.isPending ? (
            <LoadingState title="Loading portfolio" />
          ) : portfolio.isError ? (
            <ErrorState error={portfolio.error} onRetry={() => void portfolio.refetch()} />
          ) : (
            <dl className="tl-facts">
              <div>
                <dt>Tracked entities</dt>
                <dd className="tl-numeric">{portfolio.data.total_entities}</dd>
              </div>
              <div>
                <dt>Critical</dt>
                <dd className="tl-numeric">{portfolio.data.critical_entities}</dd>
              </div>
              <div>
                <dt>High risk</dt>
                <dd className="tl-numeric">{portfolio.data.high_risk_entities}</dd>
              </div>
              <div>
                <dt>Blocked</dt>
                <dd className="tl-numeric">{portfolio.data.blocked_entities}</dd>
              </div>
            </dl>
          )}
        </Card>
        <Card>
          <h2 className="tl-section-title">Recent changes</h2>
          {changes.isPending ? (
            <LoadingState title="Loading changes" />
          ) : changes.isError ? (
            <ErrorState error={changes.error} onRetry={() => void changes.refetch()} />
          ) : changes.data.changes.length === 0 ? (
            <EmptyState title="No changes detected" body="State transitions and new signals will appear here." />
          ) : (
            <ul className="tl-top-list">
              {changes.data.changes.slice(0, 5).map((change) => (
                <li key={change.change_id}>
                  <Badge tone="neutral">{change.change_type}</Badge>
                  <Link
                    className="tl-link-strong"
                    to={`/app/entities/${encodeURIComponent(change.entity_id)}`}
                  >
                    {change.entity_id}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <Section title="How to read this" description="Every item links to the entity it came from. Severity is rule-based and deterministic.">
        <Card>
          <p className="tl-body-secondary">
            Attention levels run CRITICAL → HIGH → MEDIUM → LOW. Changes carry the
            verbatim evidence that triggered them. Nothing here is generated prose —
            open an entity to inspect the underlying meetings.
          </p>
        </Card>
      </Section>
    </div>
  );
}
