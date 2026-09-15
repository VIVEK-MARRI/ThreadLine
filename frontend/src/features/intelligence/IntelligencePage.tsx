import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { useOrganisation } from "../../auth/OrganisationContext";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { PageHeader } from "../../components/layout/PageHeader";
import { Alert } from "../../components/ui/Alert";
import { relativeTime } from "../dashboard/dashboardFormat";
import type {
  AttentionResponse,
  ChangesResponse,
  PortfolioEntitySummary,
  PortfolioResponse,
} from "../../types/intelligence";
import type { MeetingSummary } from "../../types/meetings";
import {
  AttentionSignalsSection,
  ChangeStreamFilters,
  ChangeStreamSection,
  FollowUpPathsSection,
  ImpactMovementSection,
  RecentMovementSection,
  RepeatedSignalsSection,
  type SectionQuery,
} from "./IntelligenceSections";
import {
  hasActiveStreamFilters,
  parseChangeSeverity,
  parseChangeType,
  parseIntelligenceEntityType,
  selectFreshnessTimestamp,
  type IntelligenceChangeFilters,
} from "./intelligenceFormat";
import {
  useIntelligenceAttention,
  useIntelligenceChangeStream,
  useIntelligenceMeetingDirectory,
  useIntelligenceMovement,
  useIntelligencePortfolio,
  useIntelligenceRepeatedSignals,
} from "./useIntelligence";
import "./intelligence.css";

/* Intelligence workspace: organisation-level attention, changes, repeated
 * signals, dependency/impact movement, recent movement, and follow-up paths.
 * Sections load from real backend intelligence in parallel and fail
 * independently. Nothing is scored, trended, or predicted in React.
 */

export function IntelligencePage(): React.JSX.Element {
  useDocumentTitle("Intelligence");
  const { current } = useOrganisation();
  const [params, setParams] = useSearchParams();
  const organisationName = current?.organisation.name ?? "your organisation";

  const filters: IntelligenceChangeFilters = useMemo(
    () => ({
      severity: parseChangeSeverity(params.get("severity")),
      changeType: parseChangeType(params.get("change_type")),
      entityType: parseIntelligenceEntityType(params.get("entity_type")),
    }),
    [params],
  );

  const attention: SectionQuery<AttentionResponse> = useIntelligenceAttention();
  const portfolio: SectionQuery<PortfolioResponse> = useIntelligencePortfolio();
  const stream: SectionQuery<ChangesResponse> = useIntelligenceChangeStream(filters);
  const repeated: SectionQuery<ChangesResponse> = useIntelligenceRepeatedSignals();
  const movement: SectionQuery<ChangesResponse> = useIntelligenceMovement();
  const meetingsQuery = useIntelligenceMeetingDirectory();
  const meetingDirectory: Map<string, MeetingSummary> = meetingsQuery.directory;

  const entities = useMemo(() => {    const directory = new Map<string, PortfolioEntitySummary>();
    for (const entity of portfolio.data?.entities ?? []) {
      directory.set(entity.entity_id, entity);
    }
    return directory;
  }, [portfolio.data]);

  const freshness = selectFreshnessTimestamp([
    ...(attention.data?.items.map((item) => item.evaluated_at) ?? []),
    portfolio.data?.evaluated_at,
    stream.data?.evaluated_at,
    repeated.data?.evaluated_at,
    movement.data?.evaluated_at,
  ]);
  const freshnessText = freshness
    ? `Intelligence evaluated ${relativeTime(freshness) ?? freshness}.`
    : null;

  function updateFilters(next: IntelligenceChangeFilters): void {
    const updated = new URLSearchParams(params);
    if (next.severity === "ALL") updated.delete("severity");
    else updated.set("severity", next.severity);
    if (next.changeType === "ALL") updated.delete("change_type");
    else updated.set("change_type", next.changeType);
    if (next.entityType === "ALL") updated.delete("entity_type");
    else updated.set("entity_type", next.entityType);
    setParams(updated, { replace: true });
  }

  return (
    <div className="tl-page">
      <PageHeader
        title="Intelligence"
        description={`What is changing across ${organisationName}, why it matters, and where to investigate next. Every signal below comes from deterministic backend intelligence — never generated prose.`}
      />
      {freshnessText ? (
        <p className="tl-intel-freshness" role="status">
          {freshnessText}
        </p>
      ) : null}
      {portfolio.isError && !portfolio.isLoading ? (
        <Alert tone="warning" title="Organisation directory is temporarily unavailable">
          Attention and change records still link by entity ID. Names return when the portfolio
          snapshot loads again.
        </Alert>
      ) : null}
      {meetingsQuery.query.isError && !meetingsQuery.query.isLoading ? (
        <Alert tone="warning" title="Meeting titles are temporarily unavailable">
          Change and attention records still link to the correct meetings by ID.
        </Alert>
      ) : null}

      <div className="tl-intel-layout">
        <div className="tl-intel-main">
          <AttentionSignalsSection query={attention} entities={entities} />
          <ChangeStreamSection
            query={stream}
            entities={entities}
            meetings={meetingDirectory}
            filters={filters}
            controls={
              <ChangeStreamFilters
                filters={filters}
                onChange={updateFilters}
                onClear={() => setParams({}, { replace: true })}
                disabledEntityType={portfolio.isLoading || portfolio.isError}
              />
            }
          />
          <RepeatedSignalsSection query={repeated} entities={entities} meetings={meetingDirectory} />
          <ImpactMovementSection query={movement} entities={entities} meetings={meetingDirectory} />
          <RecentMovementSection query={movement} entities={entities} meetings={meetingDirectory} />
        </div>
        <div className="tl-intel-side">
          <FollowUpPathsSection query={portfolio} />
          {hasActiveStreamFilters(filters) ? (
            <p className="tl-intel-section-note" role="status">
              Stream filters apply only to the change stream above. Attention, repeated signals,
              movement, and follow-up paths remain complete.
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
