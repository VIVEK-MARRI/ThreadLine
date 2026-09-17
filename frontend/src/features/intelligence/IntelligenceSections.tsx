/* Intelligence workspace sections: attention, filtered change stream,
 * repeated signals, dependency and impact movement, recent movement, and
 * follow-up paths. Every section owns one backend question and degrades
 * independently. Related names and meeting titles come from already-loaded
 * portfolio and meeting-list responses.
 */

import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { UseQueryResult } from "@tanstack/react-query";
import { StatusBadge } from "../../components/ui/Badge";
import { Section } from "../../components/ui/Card";
import { EmptyState, ErrorState, Skeleton } from "../../components/feedback/States";
import { Field, Select } from "../../components/ui/Input";
import { attentionLevelTone, riskTone } from "../../components/ui/status";
import {
  attentionReasonLabel,
  changeTypeLabel,
  formatDateTime,
  lifecycleStateLabel,
  relativeTime,
  shortId,
} from "../dashboard/dashboardFormat";
import type {
  AttentionItem,
  ChangeSeverity,
  OrganisationChange,
  PortfolioEntitySummary,
  PortfolioResponse,
  ScanStatusResponse,
} from "../../types/intelligence";
import type { MeetingSummary } from "../../types/meetings";
import {
  INTELLIGENCE_ATTENTION_LIMIT,
  INTELLIGENCE_CHANGE_STREAM_LIMIT,
  filterChangesByEntityType,
  orderChangesNewestFirst,
  selectAttentionSignals,
  selectFollowUpEntities,
  selectImpactDependencyChanges,
  type IntelligenceChangeFilters,
  type IntelligenceEntityTypeFilter,
} from "./intelligenceFormat";

export type SectionQuery<T> = Pick<
  UseQueryResult<T>,
  "data" | "isLoading" | "isError" | "error" | "refetch"
>;

function entityHref(entityId: string): string {
  return `/app/entities/${encodeURIComponent(entityId)}`;
}

function meetingHref(meetingId: string): string {
  return `/app/meetings/${encodeURIComponent(meetingId)}`;
}

function entityName(directory: Map<string, PortfolioEntitySummary>, entityId: string): string {
  return directory.get(entityId)?.canonical_name ?? `Entity ${shortId(entityId)}`;
}

function meetingTitle(directory: Map<string, MeetingSummary>, meetingId: string): string {
  return directory.get(meetingId)?.title ?? `Meeting ${shortId(meetingId)}`;
}

function meetingDate(directory: Map<string, MeetingSummary>, meetingId: string): string | null {
  return directory.get(meetingId)?.meeting_date ?? null;
}

function severityTone(severity: ChangeSeverity): "danger" | "warning" | "info" | "neutral" {
  switch (severity) {
    case "CRITICAL":
      return "danger";
    case "HIGH":
      return "warning";
    case "MEDIUM":
      return "info";
    default:
      return "neutral";
  }
}

function SectionSkeleton({ lines = 4, label }: { lines?: number; label: string }): React.JSX.Element {
  return (
    <div className="tl-intel-loading-block">
      <Skeleton lines={lines} label={label} />
    </div>
  );
}

function ChangeTimestamp({ value }: { value: string | null }): React.JSX.Element {
  const text = relativeTime(value) ?? formatDateTime(value) ?? "Date unknown";
  return value ? (
    <time dateTime={value} title={value}>
      {text}
    </time>
  ) : (
    <span>{text}</span>
  );
}

function ChangeRow({
  change,
  entities,
  meetings,
}: {
  change: OrganisationChange;
  entities: Map<string, PortfolioEntitySummary>;
  meetings: Map<string, MeetingSummary>;
}): React.JSX.Element {
  const related = change.related_entity_ids.map((entityId) => ({
    entityId,
    name: entityName(entities, entityId),
  }));
  const path = (change.dependency_path ?? []).map((entityId) => ({
    entityId,
    name: entityName(entities, entityId),
  }));

  return (
    <li>
      <div className="tl-intel-row-head">
        <StatusBadge tone={severityTone(change.severity)} label={changeTypeLabel(change.change_type)} />
        <span>
          <ChangeTimestamp value={change.detected_at} />
        </span>
      </div>
      <p className="tl-intel-row-title">
        <Link className="tl-intel-row-title-link" to={entityHref(change.entity_id)}>
          {entityName(entities, change.entity_id)}
        </Link>
      </p>
      {related.length > 0 || path.length > 0 || change.meeting_id ? (
        <div className="tl-intel-row-actions">
          {related.length > 0 ? (
            <span>
              Related:{" "}
              {related.map((entry, index) => (
                <span key={entry.entityId}>
                  <Link className="tl-intel-link" to={entityHref(entry.entityId)}>
                    {entry.name}
                  </Link>
                  {index < related.length - 1 ? ", " : ""}
                </span>
              ))}
            </span>
          ) : null}
          {path.length > 0 ? (
            <span className="tl-intel-path">
              Path:{" "}
              {path.map((entry, index) => (
                <span key={`${change.change_id}-${entry.entityId}-${index}`}>
                  <Link className="tl-intel-link" to={entityHref(entry.entityId)}>
                    {entry.name}
                  </Link>
                  {index < path.length - 1 ? (
                    <span className="tl-intel-path-separator" aria-hidden="true">
                      {" → "}
                    </span>
                  ) : null}
                </span>
              ))}
            </span>
          ) : null}
          {change.meeting_id ? (
            <Link className="tl-intel-link" to={meetingHref(change.meeting_id)}>
              Open {meetingTitle(meetings, change.meeting_id)}
            </Link>
          ) : null}
        </div>
      ) : null}
      <details className="tl-intel-evidence">
        <summary>Why this matters</summary>
        <div className="tl-intel-evidence-body">
          <p className="tl-intel-row-detail">{change.evidence}</p>
          {change.source_text && change.source_text !== change.evidence ? (
            <blockquote className="tl-intel-quote">{change.source_text}</blockquote>
          ) : null}
          <p className="tl-intel-row-detail">
            Entity:{" "}
            <Link className="tl-intel-link" to={entityHref(change.entity_id)}>
              {entityName(entities, change.entity_id)}
            </Link>
            {change.meeting_id
              ? ` · Meeting: ${meetingTitle(meetings, change.meeting_id)}${
                  meetingDate(meetings, change.meeting_id) ?? " (date unavailable)"
                }`
              : " · No source meeting reported"}
          </p>
        </div>
      </details>
    </li>
  );
}

function ChangeList({
  changes,
  entities,
  meetings,
  emptyTitle,
  emptyBody,
}: {
  changes: OrganisationChange[];
  entities: Map<string, PortfolioEntitySummary>;
  meetings: Map<string, MeetingSummary>;
  emptyTitle: string;
  emptyBody: string;
}): React.JSX.Element {
  if (changes.length === 0) return <EmptyState title={emptyTitle} body={emptyBody} />;
  return (
    <ul className="tl-intel-rows">
      {changes.map((change) => (
        <ChangeRow key={change.change_id} change={change} entities={entities} meetings={meetings} />
      ))}
    </ul>
  );
}

export function AttentionSignalsSection({
  query,
  entities,
}: {
  query: SectionQuery<{ items: AttentionItem[] }>;
  entities: Map<string, PortfolioEntitySummary>;
}): React.JSX.Element {
  const items = selectAttentionSignals(query.data?.items, INTELLIGENCE_ATTENTION_LIMIT);
  return (
    <Section
      title="Current attention"
      description="Backend-prioritised signals, strongest first. Scores are shown with their contributing reasons."
    >
      {query.isLoading ? <SectionSkeleton lines={5} label="Loading attention signals" /> : null}
      {!query.isLoading && query.isError ? (
        <ErrorState
          title="Couldn't load attention"
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {!query.isLoading && !query.isError && items.length === 0 ? (
        <EmptyState
          title="No active attention signals"
          body="No entity currently has an actionable backend attention signal."
        />
      ) : null}
      {!query.isLoading && !query.isError && items.length > 0 ? (
        <ul className="tl-intel-rows">
          {items.map((item) => (
            <li key={item.attention_id}>
              <div className="tl-intel-row-head">
                <StatusBadge tone={attentionLevelTone(item.attention_level)} label={item.attention_level} />
                <span>Score {item.score}</span>
              </div>
              <p className="tl-intel-row-title">
                <Link className="tl-intel-row-title-link" to={entityHref(item.entity_id)}>
                  {entityName(entities, item.entity_id)}
                </Link>
              </p>
              <p className="tl-intel-row-detail">
                {item.reasons.map(attentionReasonLabel).join(" · ")}
              </p>
              <details className="tl-intel-evidence">
                <summary>Why this matters</summary>
                <div className="tl-intel-evidence-body">
                  <p className="tl-intel-row-detail">
                    {`Score ${item.score} from: ${item.reasons.map(attentionReasonLabel).join(", ")}.`}
                  </p>
                  <p className="tl-intel-row-detail">
                    Evaluated <ChangeTimestamp value={item.evaluated_at} />. Open the entity for
                    the exact observations, insights, and meeting evidence behind this signal.
                  </p>
                  <p className="tl-intel-row-detail">
                    Entity:{" "}
                    <Link className="tl-intel-link" to={entityHref(item.entity_id)}>
                      {entityName(entities, item.entity_id)}
                    </Link>
                  </p>
                </div>
              </details>
            </li>
          ))}
        </ul>
      ) : null}
    </Section>
  );
}

export function ChangeStreamFilters({
  filters,
  onChange,
  onClear,
  disabledEntityType,
}: {
  filters: IntelligenceChangeFilters;
  onChange: (filters: IntelligenceChangeFilters) => void;
  onClear: () => void;
  disabledEntityType: boolean;
}): React.JSX.Element {
  return (
    <form
      aria-label="Filter change stream"
      className="tl-intel-toolbar"
      onSubmit={(event) => event.preventDefault()}
    >
      <Field label="Severity" htmlFor="intel-severity">
        <Select
          id="intel-severity"
          value={filters.severity}
          onChange={(event) =>
            onChange({ ...filters, severity: event.target.value as IntelligenceChangeFilters["severity"] })
          }
        >
          <option value="ALL">All severities</option>
          <option value="CRITICAL">Critical</option>
          <option value="HIGH">High</option>
          <option value="MEDIUM">Medium</option>
          <option value="INFO">Info</option>
        </Select>
      </Field>
      <Field label="Change type" htmlFor="intel-change-type">
        <Select
          id="intel-change-type"
          value={filters.changeType}
          onChange={(event) =>
            onChange({ ...filters, changeType: event.target.value as IntelligenceChangeFilters["changeType"] })
          }
        >
          <option value="ALL">All change types</option>
          <option value="STATE_BLOCKED">Became blocked</option>
          <option value="STATE_RESOLVED">Resolved</option>
          <option value="STATE_REGRESSED">Regressed</option>
          <option value="STATE_REOPENED">Reopened</option>
          <option value="REPEATED_UNRESOLVED">Repeatedly unresolved</option>
          <option value="RISK_ESCALATED">Risk escalated</option>
          <option value="NEW_DEPENDENCY">New dependency</option>
          <option value="DEPENDENCY_EXPANDED">Dependency chain grew</option>
          <option value="IMPACT_EXPANDED">Impact widened</option>
          <option value="ENTITY_BECAME_STALE">Went stale</option>
        </Select>
      </Field>
      <Field
        label="Entity type"
        htmlFor="intel-entity-type"
        hint="Uses the loaded portfolio directory."
      >
        <Select
          id="intel-entity-type"
          value={filters.entityType}
          disabled={disabledEntityType}
          onChange={(event) =>
            onChange({
              ...filters,
              entityType: event.target.value as IntelligenceEntityTypeFilter,
            })
          }
        >
          <option value="ALL">All entity types</option>
          <option value="PERSON">People</option>
          <option value="ISSUE">Issues</option>
        </Select>
      </Field>
      <div className="tl-intel-toolbar-actions">
        <button type="button" className="tl-btn tl-btn-secondary tl-btn-sm" onClick={onClear}>
          Clear filters
        </button>
      </div>
    </form>
  );
}

export function ChangeStreamSection({
  query,
  entities,
  meetings,
  filters,
  controls,
}: {
  query: SectionQuery<{ changes: OrganisationChange[] }>;
  entities: Map<string, PortfolioEntitySummary>;
  meetings: Map<string, MeetingSummary>;
  filters: IntelligenceChangeFilters;
  controls: ReactNode;
}): React.JSX.Element {
  const filtered = filterChangesByEntityType(query.data?.changes ?? [], filters.entityType, entities);
  return (
    <Section
      title="Change stream"
      description={`Editorial organisation changes in backend significance order. Severity and change type use the backend query; entity type narrows these loaded records. Limited to ${INTELLIGENCE_CHANGE_STREAM_LIMIT} matching records.`}
    >
      {controls}
      {query.isLoading ? <SectionSkeleton lines={6} label="Loading change stream" /> : null}
      {!query.isLoading && query.isError ? (
        <ErrorState
          title="Couldn't load changes"
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {!query.isLoading && !query.isError && filtered.length === 0 ? (
        <EmptyState
          title="No changes match these filters"
          body="No loaded change has this severity, type, and entity-type combination. Clear the filters to see the loaded stream again."
        />
      ) : null}
      {!query.isLoading && !query.isError && filtered.length > 0 ? (
        <>
          <p className="tl-intel-result-count" role="status">
            {`Showing ${filtered.length} loaded ${filtered.length === 1 ? "change" : "changes"}.`}
          </p>
          <ChangeList
            changes={filtered}
            entities={entities}
            meetings={meetings}
            emptyTitle="No changes match these filters"
            emptyBody="No loaded change has this severity, type, and entity-type combination."
          />
        </>
      ) : null}
    </Section>
  );
}

export function RepeatedSignalsSection({
  query,
  entities,
  meetings,
}: {
  query: SectionQuery<{ changes: OrganisationChange[] }>;
  entities: Map<string, PortfolioEntitySummary>;
  meetings: Map<string, MeetingSummary>;
}): React.JSX.Element {
  const changes = query.data?.changes ?? [];
  return (
    <Section
      title="Repeated signals"
      description="Only records the backend has already classified as repeatedly unresolved."
    >
      {query.isLoading ? <SectionSkeleton lines={4} label="Loading repeated signals" /> : null}
      {!query.isLoading && query.isError ? (
        <ErrorState
          title="Couldn't load repeated signals"
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {!query.isLoading && !query.isError ? (
        <ChangeList
          changes={changes}
          entities={entities}
          meetings={meetings}
          emptyTitle="No repeated signals"
          emptyBody="The backend has not flagged any repeated unresolved observation in this organisation."
        />
      ) : null}
    </Section>
  );
}

export function ImpactMovementSection({
  query,
  entities,
  meetings,
}: {
  query: SectionQuery<{ changes: OrganisationChange[] }>;
  entities: Map<string, PortfolioEntitySummary>;
  meetings: Map<string, MeetingSummary>;
}): React.JSX.Element {
  const selected = selectImpactDependencyChanges(query.data?.changes);
  return (
    <Section
      title="Dependency and impact movement"
      description="Only backend-classified new dependencies, expanded dependency chains, and widened impacts. Observed association is not shown here as a dependency."
    >
      {query.isLoading ? <SectionSkeleton lines={4} label="Loading dependency and impact movement" /> : null}
      {!query.isLoading && query.isError ? (
        <ErrorState
          title="Couldn't load dependency and impact movement"
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {!query.isLoading && !query.isError ? (
        <ChangeList
          changes={selected}
          entities={entities}
          meetings={meetings}
          emptyTitle="No dependency or impact movement"
          emptyBody="The loaded change set contains no new dependencies, expanded chains, or widened impacts."
        />
      ) : null}
    </Section>
  );
}

export function RecentMovementSection({
  query,
  entities,
  meetings,
}: {
  query: SectionQuery<{ changes: OrganisationChange[] }>;
  entities: Map<string, PortfolioEntitySummary>;
  meetings: Map<string, MeetingSummary>;
}): React.JSX.Element {
  const changes = orderChangesNewestFirst(query.data?.changes ?? []).slice(0, 10);
  return (
    <Section
      title="Recent movement"
      description="The newest loaded changes first. Backend significance order is preserved by the change stream above."
    >
      {query.isLoading ? <SectionSkeleton lines={5} label="Loading recent movement" /> : null}
      {!query.isLoading && query.isError ? (
        <ErrorState
          title="Couldn't load recent movement"
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {!query.isLoading && !query.isError ? (
        <ChangeList
          changes={changes}
          entities={entities}
          meetings={meetings}
          emptyTitle="No recent movement"
          emptyBody="No loaded change has a usable event timestamp yet."
        />
      ) : null}
    </Section>
  );
}

export function FollowUpPathsSection({
  query,
}: {
  query: SectionQuery<PortfolioResponse>;
}): React.JSX.Element {
  const entities = selectFollowUpEntities(query.data);
  return (
    <Section
      title="Where follow-up exists"
      description="Entities the backend already associates with recommended actions. Exact recommendations live on each entity page; this workspace does not manage actions."
    >
      {query.isLoading ? <SectionSkeleton lines={4} label="Loading follow-up paths" /> : null}
      {!query.isLoading && query.isError ? (
        <ErrorState
          title="Couldn't load follow-up paths"
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {!query.isLoading && !query.isError && entities.length === 0 ? (
        <EmptyState
          title="No follow-up paths"
          body="The portfolio does not currently associate recommended actions with any entity."
        />
      ) : null}
      {!query.isLoading && !query.isError && entities.length > 0 ? (
        <ul className="tl-intel-rows">
          {entities.map((entity) => (
            <li key={entity.entity_id}>
              <div className="tl-intel-row-head">
                <Link className="tl-intel-row-title-link" to={entityHref(entity.entity_id)}>
                  {entity.canonical_name}
                </Link>
                <StatusBadge
                  tone={riskTone(entity.risk_level.toLowerCase() as "low" | "medium" | "high" | "critical")}
                  label={entity.risk_level}
                />
              </div>
              <p className="tl-intel-row-detail">
                {`${entity.action_count} recommended ${entity.action_count === 1 ? "action" : "actions"} · ${
                  entity.impact_count
                } impact ${entity.impact_count === 1 ? "association" : "associations"} · ${
                  entity.active_insight_count
                } active ${entity.active_insight_count === 1 ? "insight" : "insights"} · ${
                  lifecycleStateLabel(entity.current_state)
                }`}
              </p>
            </li>
          ))}
        </ul>
      ) : null}
    </Section>
  );
}

/* Proactive scan status (Stage 34, E10): the latest durable scan only.
 * Distinguishes "observed previously" (signal_count), "newly detected"
 * (new_signal_ids from the backend diff), and "not scanned yet". Every
 * timestamp and count comes from the backend scan row — nothing invented.
 */
export function ScanStatusSection({
  query,
}: {
  query: SectionQuery<ScanStatusResponse>;
}): React.JSX.Element {
  const status = query.data;
  return (
    <Section
      title="Proactive scan"
      description="The latest durable organisation scan. New signals are identifiers the backend saw for the first time in that scan."
    >
      {query.isLoading ? <SectionSkeleton lines={3} label="Loading scan status" /> : null}
      {!query.isLoading && query.isError ? (
        <ErrorState
          title="Couldn't load scan status"
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {!query.isLoading && !query.isError && (!status || !status.scanned) ? (
        <EmptyState
          title="No proactive scan yet"
          body="The backend has not completed a proactive intelligence scan for this organisation. Existing attention, changes, and portfolio sections are unaffected."
        />
      ) : null}
      {!query.isLoading && !query.isError && status?.scanned ? (
        <div>
          <p className="tl-intel-row-detail">
            {`Last scan ${status.completed_at ? (relativeTime(status.completed_at) ?? formatDateTime(status.completed_at)) : "at an unknown time"} — ${status.signal_count ?? 0} signals observed, ${status.new_signal_count ?? 0} newly detected.`}
          </p>
          {(status.new_signal_ids ?? []).length > 0 ? (
            <ul className="tl-intel-rows">
              {status.new_signal_ids.slice(0, 10).map((signalId) => (
                <li key={signalId}>
                  <div className="tl-intel-row-head">
                    <StatusBadge tone="info" label="NEW" />
                    <span className="tl-intel-row-title">{signalId}</span>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="tl-intel-row-detail">
              No new signals since the previous scan — earlier findings are unchanged.
            </p>
          )}
        </div>
      ) : null}
    </Section>
  );
}
