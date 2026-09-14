/* Dashboard sections: attention, changes, processing, risks, explore.
 *
 * Each section owns one backend question and degrades alone: loading shows
 * a structural skeleton, failure shows an ErrorState with retry, and empty
 * shows calm, accurate copy. Sections never invent data — every row links
 * to a real entity or meeting route.
 */

import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import type { UseQueryResult } from "@tanstack/react-query";
import { Section } from "../../components/ui/Card";
import { StatusBadge } from "../../components/ui/Badge";
import { attentionLevelTone, riskTone } from "../../components/ui/status";
import { EmptyState, ErrorState, Skeleton } from "../../components/feedback/States";
import type {
  AttentionItem,
  AttentionResponse,
  ChangesResponse,
  OrganisationChange,
  PortfolioEntitySummary,
  PortfolioResponse,
} from "../../types/intelligence";
import type { JobHealth } from "../../types/jobs";
import type { EntityDirectoryEntry } from "./useDashboard";
import {
  attentionReasonLabel,
  changeTypeLabel,
  formatDateTime,
  formatDuration,
  lifecycleStateLabel,
  relativeTime,
  shortId,
} from "./dashboardFormat";

export type SectionState<T> = Pick<
  UseQueryResult<T>,
  "data" | "isLoading" | "isError" | "error" | "refetch"
>;

function entityHref(entityId: string): string {
  return `/app/entities/${encodeURIComponent(entityId)}`;
}

function meetingHref(meetingId: string): string {
  return `/app/meetings/${encodeURIComponent(meetingId)}`;
}

function entityName(directory: Map<string, EntityDirectoryEntry>, entityId: string): string {
  return directory.get(entityId)?.displayName ?? shortId(entityId);
}

function levelLabel(level: string): string {
  return level.charAt(0) + level.slice(1).toLowerCase();
}

function severityTone(severity: string): "danger" | "warning" | "info" | "neutral" {
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

function SectionSkeleton({ lines = 4 }: { lines?: number }): React.JSX.Element {
  return (
    <div className="tl-dash-loading">
      <Skeleton lines={lines} />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Attention                                                           */
/* ------------------------------------------------------------------ */

export function AttentionSection({
  query,
  directory,
  limit,
}: {
  query: SectionState<AttentionResponse>;
  directory: Map<string, EntityDirectoryEntry>;
  limit: number;
}): React.JSX.Element {
  const items = (query.data?.items ?? []).slice(0, limit);
  return (
    <Section title="Needs attention" description="Entities asking for a decision or follow-up, most urgent first.">
      {query.isLoading ? <SectionSkeleton lines={5} /> : null}
      {!query.isLoading && query.isError ? (
        <ErrorState
          title="Couldn't load attention"
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {!query.isLoading && !query.isError && items.length === 0 ? (
        <EmptyState
          title="Nothing needs attention"
          body="No entities currently show actionable signals. New blocks, escalations, and stale work will surface here."
        />
      ) : null}
      {!query.isLoading && !query.isError && items.length > 0 ? (
        <ul className="tl-dash-rows">
          {items.map((item: AttentionItem) => (
            <li key={item.attention_id} className="tl-dash-row">
              <StatusBadge tone={attentionLevelTone(item.attention_level)} label={levelLabel(item.attention_level)} />
              <span className="tl-dash-row-body">
                <Link className="tl-dash-row-title" to={entityHref(item.entity_id)}>
                  {entityName(directory, item.entity_id)}
                </Link>
                <span className="tl-dash-row-meta">
                  {item.reasons.map(attentionReasonLabel).join(" · ")}
                </span>
              </span>
              <ChevronRight aria-hidden="true" className="tl-dash-row-go" />
            </li>
          ))}
        </ul>
      ) : null}
    </Section>
  );
}

/* ------------------------------------------------------------------ */
/* Recent changes                                                      */
/* ------------------------------------------------------------------ */

export function ChangesSection({
  query,
  directory,
  meetingTitles,
}: {
  query: SectionState<ChangesResponse>;
  directory: Map<string, EntityDirectoryEntry>;
  meetingTitles: Map<string, string>;
}): React.JSX.Element {
  const changes = query.data?.changes ?? [];
  return (
    <Section title="Recent changes" description="What changed across the organisation, most significant first.">
      {query.isLoading ? <SectionSkeleton lines={6} /> : null}
      {!query.isLoading && query.isError ? (
        <ErrorState
          title="Couldn't load recent changes"
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {!query.isLoading && !query.isError && changes.length === 0 ? (
        <EmptyState
          title="No significant organisational changes yet"
          body="State transitions, escalations, and new dependencies will appear here as meetings are processed."
        />
      ) : null}
      {!query.isLoading && !query.isError && changes.length > 0 ? (
        <ul className="tl-dash-rows">
          {changes.map((change: OrganisationChange) => (
            <ChangeRow
              key={change.change_id}
              change={change}
              directory={directory}
              meetingTitles={meetingTitles}
            />
          ))}
        </ul>
      ) : null}
    </Section>
  );
}

function ChangeRow({
  change,
  directory,
  meetingTitles,
}: {
  change: OrganisationChange;
  directory: Map<string, EntityDirectoryEntry>;
  meetingTitles: Map<string, string>;
}): React.JSX.Element {
  const when = relativeTime(change.detected_at) ?? formatDateTime(change.detected_at) ?? "date unknown";
  return (
    <li className="tl-dash-row tl-dash-row-change">
      <StatusBadge tone={severityTone(change.severity)} label={changeTypeLabel(change.change_type)} />
      <span className="tl-dash-row-body">
        <Link className="tl-dash-row-title" to={entityHref(change.entity_id)}>
          {entityName(directory, change.entity_id)}
        </Link>
        <span className="tl-dash-row-meta">{when}</span>
        <span className="tl-dash-evidence">{change.evidence}</span>
        {change.meeting_id ? (
          <Link className="tl-dash-meeting-link" to={meetingHref(change.meeting_id)}>
            Discussed in {meetingTitles.get(change.meeting_id) ?? `meeting ${shortId(change.meeting_id)}`}
          </Link>
        ) : null}
      </span>
    </li>
  );
}

/* ------------------------------------------------------------------ */
/* Processing + active work                                            */
/* ------------------------------------------------------------------ */

export interface DiscussedMeeting {
  meeting_id: string;
  title: string | null;
}

export function ProcessingSection({
  query,
  discussed,
}: {
  query: SectionState<JobHealth>;
  discussed: DiscussedMeeting[];
}): React.JSX.Element {
  const counts = query.data?.counts ?? {};
  const queued = (counts.PENDING ?? 0) + (counts.RETRY_WAITING ?? 0);
  const running = counts.RUNNING ?? 0;
  const failed = counts.FAILED ?? 0;
  const staleRunning = query.data?.stale_running ?? 0;
  const workerOn = query.data?.worker_enabled ?? true;
  const oldestWait = formatDuration(query.data?.oldest_pending_age_seconds);
  const active = queued + running > 0;

  const statusLine = !query.data
    ? null
    : !workerOn
      ? "Background worker is off — ingested meetings will wait in the queue."
      : active
        ? [
            queued > 0 ? `${queued} queued` : null,
            running > 0 ? `${running} processing` : null,
            oldestWait && queued > 0 ? `longest wait ${oldestWait}` : null,
          ]
            .filter(Boolean)
            .join(" · ")
        : "No meetings in the queue.";

  return (
    <Section title="Active work" description="Meeting processing, live from the job queue.">
      {query.isLoading ? <SectionSkeleton lines={3} /> : null}
      {!query.isLoading && query.isError ? (
        <ErrorState
          title="Couldn't load processing status"
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {!query.isLoading && !query.isError ? (
        <div className="tl-dash-proc" role="status">
          <span
            className={[
              "tl-status-dot",
              running > 0 ? "tl-status-dot-pulse" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            aria-hidden="true"
          />
          <span>{statusLine}</span>
        </div>
      ) : null}
      {!query.isLoading && !query.isError && (failed > 0 || staleRunning > 0) ? (
        <p className="tl-dash-warn">
          {[failed > 0 ? `${failed} failed` : null, staleRunning > 0 ? `${staleRunning} stale` : null]
            .filter(Boolean)
            .join(" · ")}{" "}
          — inspect the queue before ingesting more.
        </p>
      ) : null}
      {!query.isLoading && !query.isError && discussed.length > 0 ? (
        <ul className="tl-dash-rows tl-dash-meetings">
          {discussed.map((meeting) => (
            <li key={meeting.meeting_id} className="tl-dash-row">
              <span className="tl-dash-row-body">
                <Link className="tl-dash-row-title" to={meetingHref(meeting.meeting_id)}>
                  {meeting.title ?? `Meeting ${shortId(meeting.meeting_id)}`}
                </Link>
                <span className="tl-dash-row-meta">Referenced by recent changes</span>
              </span>
              <ChevronRight aria-hidden="true" className="tl-dash-row-go" />
            </li>
          ))}
        </ul>
      ) : null}
    </Section>
  );
}

/* ------------------------------------------------------------------ */
/* Risks & blockers                                                    */
/* ------------------------------------------------------------------ */

export function RisksSection({
  query,
  entities,
}: {
  query: SectionState<PortfolioResponse>;
  entities: PortfolioEntitySummary[];
}): React.JSX.Element {
  return (
    <Section title="Risks & blockers" description="What could negatively affect the organisation.">
      {query.isLoading ? <SectionSkeleton lines={4} /> : null}
      {!query.isLoading && query.isError ? (
        <ErrorState
          title="Couldn't load risks"
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {!query.isLoading && !query.isError && entities.length === 0 ? (
        <EmptyState
          title="No elevated risks"
          body="Blocked work and high-risk entities will appear here."
        />
      ) : null}
      {!query.isLoading && !query.isError && entities.length > 0 ? (
        <ul className="tl-dash-rows">
          {entities.map((entity) => {
            const factors = [
              entity.impact_count > 0
                ? `${entity.impact_count} impact ${entity.impact_count === 1 ? "association" : "associations"}`
                : null,
              entity.action_count > 0
                ? `${entity.action_count} recommended ${entity.action_count === 1 ? "action" : "actions"}`
                : null,
            ].filter(Boolean);
            return (
              <li key={entity.entity_id} className="tl-dash-row">
                <StatusBadge tone={riskTone(entity.risk_level.toLowerCase() as "low" | "medium" | "high" | "critical")} label={levelLabel(entity.risk_level)} />
                <span className="tl-dash-row-body">
                  <Link className="tl-dash-row-title" to={entityHref(entity.entity_id)}>
                    {entity.canonical_name}
                  </Link>
                  <span className="tl-dash-row-meta">
                    {[lifecycleStateLabel(entity.current_state), ...factors].join(" · ")}
                  </span>
                </span>
                <ChevronRight aria-hidden="true" className="tl-dash-row-go" />
              </li>
            );
          })}
        </ul>
      ) : null}
    </Section>
  );
}

/* ------------------------------------------------------------------ */
/* Explore                                                             */
/* ------------------------------------------------------------------ */

const EXPLORE_LINKS = [
  { to: "/app/meetings", title: "Meetings", body: "Ingest transcripts and open meeting detail." },
  { to: "/app/entities", title: "Entities", body: "Browse people, issues, and commitments." },
  { to: "/app/intelligence", title: "Intelligence", body: "Attention, portfolio, and change analysis." },
  { to: "/app/ask", title: "Ask", body: "Questions answered with cited evidence." },
] as const;

export function ExploreSection({ freshness }: { freshness: string | null }): React.JSX.Element {
  return (
    <Section title="Explore" description="Everything else in the workspace.">
      <ul className="tl-dash-explore">
        {EXPLORE_LINKS.map((link) => (
          <li key={link.to}>
            <Link className="tl-dash-explore-link" to={link.to}>
              <span className="tl-dash-row-title">{link.title}</span>
              <span className="tl-dash-row-meta">{link.body}</span>
            </Link>
          </li>
        ))}
      </ul>
      {freshness ? <p className="tl-dash-freshness">Intelligence evaluated {freshness}.</p> : null}
    </Section>
  );
}
