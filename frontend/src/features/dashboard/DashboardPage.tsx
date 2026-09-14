import { useMemo } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { useOrganisation } from "../../auth/OrganisationContext";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { PageHeader } from "../../components/layout/PageHeader";
import type { PortfolioEntitySummary } from "../../types/intelligence";
import {
  DASHBOARD_ATTENTION_LIMIT,
  DASHBOARD_MEETINGS_LIMIT,
  DASHBOARD_RISKS_LIMIT,
  useDashboardAttention,
  useDashboardChanges,
  useDashboardJobHealth,
  useDashboardMeetingTitles,
  useDashboardPortfolio,
  useEntityDirectory,
} from "./useDashboard";
import {
  displayNameFromEmail,
  greetingFor,
  relativeTime,
  snapshotLine,
} from "./dashboardFormat";
import {
  AttentionSection,
  ChangesSection,
  ExploreSection,
  ProcessingSection,
  RisksSection,
  type DiscussedMeeting,
} from "./DashboardSections";
import "./dashboard.css";

/* Dashboard / organisation home: what needs attention, what changed,
 * what is processing, and what risks the organisation — all from real
 * backend intelligence. Sections fail independently; nothing is invented.
 */

function selectRiskEntities(entities: PortfolioEntitySummary[]): PortfolioEntitySummary[] {
  const blocked = entities.filter((entity) => entity.current_state === "BLOCKED");
  const elevated = entities.filter(
    (entity) =>
      entity.current_state !== "BLOCKED" &&
      (entity.risk_level === "CRITICAL" || entity.risk_level === "HIGH") &&
      entity.impact_count > 0,
  );
  const seen = new Set<string>();
  const ordered: PortfolioEntitySummary[] = [];
  for (const entity of [...blocked, ...elevated]) {
    if (seen.has(entity.entity_id)) continue;
    seen.add(entity.entity_id);
    ordered.push(entity);
    if (ordered.length >= DASHBOARD_RISKS_LIMIT) break;
  }
  return ordered;
}

export function DashboardPage(): React.JSX.Element {
  useDocumentTitle("Dashboard");
  const { user } = useAuth();
  const { current } = useOrganisation();
  const orgName = current?.organisation.name ?? "your organisation";

  const attention = useDashboardAttention();
  const portfolio = useDashboardPortfolio();
  const changes = useDashboardChanges();
  const jobs = useDashboardJobHealth();
  const directory = useEntityDirectory();

  const changeMeetingIds = useMemo(
    () =>
      (changes.data?.changes ?? [])
        .map((change) => change.meeting_id)
        .filter((id): id is string => Boolean(id)),
    [changes.data],
  );
  const meetingTitles = useDashboardMeetingTitles(changeMeetingIds);

  const discussed: DiscussedMeeting[] = useMemo(() => {
    const seen = new Set<string>();
    const ordered: DiscussedMeeting[] = [];
    for (const meetingId of changeMeetingIds) {
      if (seen.has(meetingId)) continue;
      seen.add(meetingId);
      ordered.push({ meeting_id: meetingId, title: meetingTitles.get(meetingId) ?? null });
      if (ordered.length >= DASHBOARD_MEETINGS_LIMIT) break;
    }
    return ordered;
  }, [changeMeetingIds, meetingTitles]);

  const riskEntities = useMemo(
    () => selectRiskEntities(portfolio.data?.entities ?? []),
    [portfolio.data],
  );

  const greetingName = displayNameFromEmail(user?.email);
  const title = greetingName ? `${greetingFor()}, ${greetingName}` : greetingFor();

  const snapshot =
    portfolio.data && attention.data
      ? snapshotLine({
          totalEntities: portfolio.data.total_entities,
          attentionCount: attention.data.entity_count,
          blockedCount: portfolio.data.blocked_entities,
        })
      : null;

  const freshness = useMemo(() => {
    const stamps = [portfolio.data?.evaluated_at, changes.data?.evaluated_at].filter(
      (stamp): stamp is string => Boolean(stamp),
    );
    if (stamps.length === 0) return null;
    const latest = stamps.reduce((a, b) => (Date.parse(a) >= Date.parse(b) ? a : b));
    return relativeTime(latest);
  }, [portfolio.data, changes.data]);

  return (
    <div className="tl-page">
      <PageHeader
        title={title}
        description={
          snapshot
            ? `Organisation overview for ${orgName} — ${snapshot}.`
            : `Organisation overview for ${orgName}.`
        }
        actions={
          <Link className="tl-btn tl-btn-primary" to="/app/meetings">
            Ingest a meeting
          </Link>
        }
      />

      <div className="tl-dash-grid">
        <AttentionSection query={attention} directory={directory} limit={DASHBOARD_ATTENTION_LIMIT} />
        <ChangesSection query={changes} directory={directory} meetingTitles={meetingTitles} />
      </div>

      <ProcessingSection query={jobs} discussed={discussed} />

      <div className="tl-dash-grid">
        <RisksSection query={portfolio} entities={riskEntities} />
        <ExploreSection freshness={freshness} />
      </div>
    </div>
  );
}
