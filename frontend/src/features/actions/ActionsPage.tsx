import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { intelligenceApi } from "../../api/intelligence";
import { queryKeys } from "../../api/keys";
import { useOrganisation } from "../../auth/OrganisationContext";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { StatusBadge } from "../../components/ui/Badge";
import { Card, Section } from "../../components/ui/Card";
import { PageHeader } from "../../components/layout/PageHeader";
import { EmptyState, ErrorState, Skeleton } from "../../components/feedback/States";
import { riskTone } from "../../components/ui/status";
import { lifecycleStateLabel } from "../dashboard/dashboardFormat";
import { selectFollowUpEntities } from "../intelligence/intelligenceFormat";
import "./actions.css";

/* Follow-up paths: the portfolio already reports which entities have
 * backend-recommended actions. This workspace links to those entity pages
 * for the exact recommendations; it does not manage, prioritise, or restate
 * actions itself.
 */

export function ActionsPage(): React.JSX.Element {
  useDocumentTitle("Actions");
  const { organisationId } = useOrganisation();
  const portfolio = useQuery({
    queryKey: queryKeys.portfolio(organisationId ?? ""),
    queryFn: () => intelligenceApi.portfolio(),
    enabled: Boolean(organisationId),
    staleTime: 30_000,
  });
  const followUps = selectFollowUpEntities(portfolio.data);

  return (
    <div className="tl-page">
      <PageHeader
        title="Actions"
        description="Entities that the portfolio backend already associates with recommended actions. Open an entity to read its exact recommendation; this workspace does not create or manage follow-up work."
        actions={
          <Link className="tl-btn tl-btn-secondary" to="/app/intelligence">
            Review intelligence
          </Link>
        }
      />
      <Section
        title="Follow-up paths"
        description="Backend-reported action counts only. No priorities, owners, due dates, or statuses are shown here."
      >
        {portfolio.isPending ? (
          <div className="tl-action-loading">
            <Skeleton lines={5} label="Loading follow-up paths" />
          </div>
        ) : null}
        {!portfolio.isPending && portfolio.isError ? (
          <ErrorState
            title="Couldn't load follow-up paths"
            error={portfolio.error}
            onRetry={() => void portfolio.refetch()}
          />
        ) : null}
        {!portfolio.isPending && !portfolio.isError && followUps.length === 0 ? (
          <EmptyState
            title="No follow-up paths"
            body="The portfolio backend currently associates recommended actions with no entity in this organisation."
          />
        ) : null}
        {!portfolio.isPending && !portfolio.isError && followUps.length > 0 ? (
          <Card padded={false}>
            <ul className="tl-action-list">
              {followUps.map((entity) => (
                <li key={entity.entity_id}>
                  <StatusBadge
                    tone={riskTone(
                      entity.risk_level.toLowerCase() as "low" | "medium" | "high" | "critical",
                    )}
                    label={entity.risk_level}
                  />
                  <span className="tl-action-body">
                    <Link className="tl-action-title" to={`/app/entities/${encodeURIComponent(entity.entity_id)}`}>
                      {entity.canonical_name}
                    </Link>
                    <span className="tl-action-meta">
                      {`${entity.action_count} recommended ${
                        entity.action_count === 1 ? "action" : "actions"
                      } · ${lifecycleStateLabel(entity.current_state)} · ${
                        entity.impact_count
                      } impact ${entity.impact_count === 1 ? "association" : "associations"}`}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        ) : null}
      </Section>
    </div>
  );
}
