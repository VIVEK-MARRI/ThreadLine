/* Intelligence workspace presentation helpers.
 *
 * Pure mappings of backend-controlled organisation intelligence vocabularies
 * and real timestamps to readable copy. Backend ordering is preserved unless
 * the UI explicitly labels a newest-first movement view. No thresholds, risk
 * derivation, trend detection, probabilities, or causal claims live here.
 */

import type {
  AttentionItem,
  ChangeSeverity,
  OrganisationChange,
  PortfolioEntitySummary,
  PortfolioResponse,
} from "../../types/intelligence";
import type { MeetingSummary } from "../../types/meetings";

export const INTELLIGENCE_ATTENTION_LIMIT = 10;
export const INTELLIGENCE_CHANGE_STREAM_LIMIT = 50;
export const INTELLIGENCE_REPEATED_LIMIT = 25;
export const INTELLIGENCE_MOVEMENT_LIMIT = 100;
export const INTELLIGENCE_MEETING_DIRECTORY_LIMIT = 100;
export const INTELLIGENCE_FOLLOW_UP_LIMIT = 8;

export type ChangeSeverityFilter = "ALL" | ChangeSeverity;
export type ChangeTypeFilter = "ALL" | string;
export type IntelligenceEntityTypeFilter = "ALL" | "PERSON" | "ISSUE";

export interface IntelligenceChangeFilters {
  severity: ChangeSeverityFilter;
  changeType: ChangeTypeFilter;
  entityType: IntelligenceEntityTypeFilter;
}

export const CHANGE_SEVERITY_FILTERS: Array<{ value: ChangeSeverityFilter; label: string }> = [
  { value: "ALL", label: "All severities" },
  { value: "CRITICAL", label: "Critical" },
  { value: "HIGH", label: "High" },
  { value: "MEDIUM", label: "Medium" },
  { value: "INFO", label: "Info" },
];

export const CHANGE_TYPE_FILTERS: Array<{ value: ChangeTypeFilter; label: string }> = [
  { value: "ALL", label: "All change types" },
  { value: "STATE_OPENED", label: "Opened" },
  { value: "STATE_STARTED", label: "Started" },
  { value: "STATE_BLOCKED", label: "Became blocked" },
  { value: "STATE_RESOLVED", label: "Resolved" },
  { value: "STATE_REGRESSED", label: "Regressed" },
  { value: "STATE_REOPENED", label: "Reopened" },
  { value: "REPEATED_UNRESOLVED", label: "Repeatedly unresolved" },
  { value: "RISK_ESCALATED", label: "Risk escalated" },
  { value: "RISK_DEESCALATED", label: "Risk cleared" },
  { value: "NEW_DEPENDENCY", label: "New dependency" },
  { value: "DEPENDENCY_EXPANDED", label: "Dependency chain grew" },
  { value: "IMPACT_EXPANDED", label: "Impact widened" },
  { value: "ENTITY_BECAME_STALE", label: "Went stale" },
];

export const INTELLIGENCE_ENTITY_TYPE_FILTERS: Array<{
  value: IntelligenceEntityTypeFilter;
  label: string;
}> = [
  { value: "ALL", label: "All entity types" },
  { value: "PERSON", label: "People" },
  { value: "ISSUE", label: "Issues" },
];

const CHANGE_TYPE_VALUES = new Set(
  CHANGE_TYPE_FILTERS.filter((option) => option.value !== "ALL").map((option) => option.value),
);

export function parseChangeSeverity(value: string | null): ChangeSeverityFilter {
  if (value === "CRITICAL" || value === "HIGH" || value === "MEDIUM" || value === "INFO") {
    return value;
  }
  return "ALL";
}

export function parseChangeType(value: string | null): ChangeTypeFilter {
  if (value && CHANGE_TYPE_VALUES.has(value)) return value;
  return "ALL";
}

export function parseIntelligenceEntityType(
  value: string | null,
): IntelligenceEntityTypeFilter {
  if (value === "PERSON" || value === "ISSUE") return value;
  return "ALL";
}

export function entityDisplayName(
  directory: Map<string, PortfolioEntitySummary>,
  entityId: string,
): string {
  return directory.get(entityId)?.canonical_name ?? entityId;
}

export function meetingDisplay(
  directory: Map<string, MeetingSummary>,
  meetingId: string,
): { title: string; date: string | null } {
  const meeting = directory.get(meetingId);
  if (!meeting) return { title: `Meeting ${meetingId.slice(0, 8)}…`, date: null };
  return { title: meeting.title, date: meeting.meeting_date };
}

export function selectAttentionSignals(
  items: AttentionItem[] | undefined,
  limit: number = INTELLIGENCE_ATTENTION_LIMIT,
): AttentionItem[] {
  return (items ?? []).slice(0, Math.max(0, limit));
}

export function selectImpactDependencyChanges(
  changes: OrganisationChange[] | undefined,
): OrganisationChange[] {
  return (changes ?? []).filter(
    (change) =>
      change.change_type === "NEW_DEPENDENCY" ||
      change.change_type === "DEPENDENCY_EXPANDED" ||
      change.change_type === "IMPACT_EXPANDED",
  );
}

export function filterChangesByEntityType(
  changes: OrganisationChange[],
  entityType: IntelligenceEntityTypeFilter,
  directory: Map<string, PortfolioEntitySummary>,
): OrganisationChange[] {
  if (entityType === "ALL") return changes;
  return changes.filter((change) => directory.get(change.entity_id)?.entity_type === entityType);
}

export function orderChangesNewestFirst(changes: OrganisationChange[]): OrganisationChange[] {
  return [...changes].sort((first, second) => {
    const firstTime = first.detected_at ? Date.parse(first.detected_at) : Number.NaN;
    const secondTime = second.detected_at ? Date.parse(second.detected_at) : Number.NaN;
    const firstValid = !Number.isNaN(firstTime);
    const secondValid = !Number.isNaN(secondTime);
    if (firstValid && secondValid) return secondTime - firstTime;
    if (firstValid) return -1;
    if (secondValid) return 1;
    return 0;
  });
}

export function selectFollowUpEntities(
  portfolio: PortfolioResponse | undefined,
  limit: number = INTELLIGENCE_FOLLOW_UP_LIMIT,
): PortfolioEntitySummary[] {
  return (portfolio?.entities ?? [])
    .filter((entity) => entity.action_count > 0)
    .slice(0, Math.max(0, limit));
}

export function selectMeetingDirectory(
  meetings: MeetingSummary[] | undefined,
): Map<string, MeetingSummary> {
  const directory = new Map<string, MeetingSummary>();
  for (const meeting of meetings ?? []) {
    if (!directory.has(meeting.meeting_id)) directory.set(meeting.meeting_id, meeting);
  }
  return directory;
}

export function selectFreshnessTimestamp(
  timestamps: Array<string | null | undefined>,
): string | null {
  let latest: number | null = null;
  let latestIso: string | null = null;
  for (const timestamp of timestamps) {
    if (!timestamp) continue;
    const time = Date.parse(timestamp);
    if (Number.isNaN(time)) continue;
    if (latest === null || time > latest) {
      latest = time;
      latestIso = timestamp;
    }
  }
  return latestIso;
}

export function hasActiveStreamFilters(filters: IntelligenceChangeFilters): boolean {
  return filters.severity !== "ALL" || filters.changeType !== "ALL" || filters.entityType !== "ALL";
}
