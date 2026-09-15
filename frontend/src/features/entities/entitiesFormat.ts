/* Entity workspace presentation helpers.
 *
 * Pure mappings of backend-controlled entity vocabularies and real timestamps
 * to readable copy. No thresholds, classification, risk scoring, summaries,
 * or inferred relationships live here.
 */

import type { StatusTone } from "../../components/ui/status";
import type {
  AttentionLevel,
  AttentionReason,
  EntityAttention,
  EntityInsight,
  EntityRelationship,
  MemoryFactType,
  TemporalState,
  TimelineEvent,
} from "../../types/entities";
import type { PortfolioEntitySummary } from "../../types/intelligence";
import type { EntityResponse, EntityType } from "../../types/entities";

export type EntityDirectoryTypeFilter = "ALL" | EntityType;
export type EntityDirectoryStateFilter = "ALL" | TemporalState;
export type EntityDirectorySort = "attention" | "name" | "observations";

export interface EntityDirectoryFilters {
  query: string;
  type: EntityDirectoryTypeFilter;
  state: EntityDirectoryStateFilter;
  sort: EntityDirectorySort;
}

export interface EntityDirectoryRow {
  entity: EntityResponse;
  summary: PortfolioEntitySummary | null;
}

export const ENTITY_DIRECTORY_TYPE_OPTIONS: Array<{ value: EntityDirectoryTypeFilter; label: string }> = [
  { value: "ALL", label: "All types" },
  { value: "PERSON", label: "People" },
  { value: "ISSUE", label: "Issues" },
];

export const ENTITY_DIRECTORY_STATE_OPTIONS: Array<{ value: EntityDirectoryStateFilter; label: string }> = [
  { value: "ALL", label: "All states" },
  { value: "UNKNOWN", label: "Unknown" },
  { value: "OPEN", label: "Open" },
  { value: "IN_PROGRESS", label: "In progress" },
  { value: "BLOCKED", label: "Blocked" },
  { value: "RESOLVED", label: "Resolved" },
];

export const ENTITY_DIRECTORY_SORT_OPTIONS: Array<{ value: EntityDirectorySort; label: string }> = [
  { value: "attention", label: "Needs attention first" },
  { value: "name", label: "Name A–Z" },
  { value: "observations", label: "Most observed" },
];

const TEMPORAL_STATES: TemporalState[] = ["UNKNOWN", "OPEN", "IN_PROGRESS", "BLOCKED", "RESOLVED"];

export function parseEntityDirectoryType(value: string | null): EntityDirectoryTypeFilter {
  return value === "PERSON" || value === "ISSUE" ? value : "ALL";
}

export function parseEntityDirectoryState(value: string | null): EntityDirectoryStateFilter {
  return TEMPORAL_STATES.includes(value as TemporalState) ? (value as TemporalState) : "ALL";
}

export function parseEntityDirectorySort(value: string | null): EntityDirectorySort {
  if (value === "name" || value === "observations") return value;
  return "attention";
}

export function normalizeEntityQuery(value: string): string {
  return value.trim().toLowerCase();
}

export function entitySearchText(entity: EntityResponse): string {
  return [entity.canonical_name, entity.entity_id, ...(entity.aliases ?? [])]
    .join("\n")
    .toLowerCase();
}

export function temporalStateLabel(state: TemporalState | string | null | undefined): string {
  switch (state) {
    case "UNKNOWN":
      return "Unknown";
    case "OPEN":
      return "Open";
    case "IN_PROGRESS":
      return "In progress";
    case "BLOCKED":
      return "Blocked";
    case "RESOLVED":
      return "Resolved";
    default:
      return typeof state === "string" && state.trim() !== ""
        ? state.replace(/_/g, " ").toLowerCase()
        : "Not assessed";
  }
}

export function temporalStateTone(state: TemporalState | string | null | undefined): StatusTone {
  switch (state) {
    case "BLOCKED":
      return "danger";
    case "RESOLVED":
      return "success";
    case "IN_PROGRESS":
    case "OPEN":
      return "info";
    default:
      return "neutral";
  }
}

export function attentionLevelLabel(level: AttentionLevel | string | null | undefined): string {
  switch (level) {
    case "CRITICAL":
      return "Critical";
    case "HIGH":
      return "High";
    case "MEDIUM":
      return "Medium";
    case "LOW":
      return "Low";
    default:
      return "No attention signals";
  }
}

export function attentionReasonLabel(reason: AttentionReason): string {
  switch (reason) {
    case "ENTITY_BLOCKED":
      return "Blocked entity";
    case "REOPEN_ATTEMPT":
      return "Reopen attempt";
    case "ENTITY_STALE":
      return "Stale entity";
    case "RECENT_STATE_CHANGE":
      return "Recent state change";
    case "REPEATED_OBSERVATION":
      return "Repeated observation";
    case "UNKNOWN_STATE":
      return "Unknown state";
  }
}

export function insightSeverityTone(severity: string): StatusTone {
  switch (severity) {
    case "CRITICAL":
      return "danger";
    case "WARNING":
      return "warning";
    default:
      return "neutral";
  }
}

export function actionPriorityTone(priority: string): StatusTone {
  switch (priority) {
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

export function actionTypeLabel(actionType: string): string {
  switch (actionType) {
    case "ESCALATE":
      return "Escalate";
    case "REQUEST_UPDATE":
      return "Request update";
    case "INVESTIGATE":
      return "Investigate";
    case "FOLLOW_UP":
      return "Follow up";
    case "REVIEW":
      return "Review";
    case "NO_ACTION":
      return "No action required";
    default:
      return actionType.replace(/_/g, " ").toLowerCase();
  }
}

export function impactLevelTone(level: string): StatusTone {
  switch (level) {
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

export function memoryFactLabel(factType: MemoryFactType): string {
  switch (factType) {
    case "FIRST_OBSERVED":
      return "First observed";
    case "LAST_OBSERVED":
      return "Last observed";
    case "CURRENT_STATE":
      return "Current state";
    case "STATE_TRANSITION":
      return "State transition";
    case "REPEATED_OBSERVATION":
      return "Repeated observation";
  }
}

export function evidenceTypeLabel(evidenceType: string): string {
  switch (evidenceType) {
    case "EXPLICIT_STATEMENT":
      return "Explicit statement";
    case "CO_OCCURRENCE":
      return "Meeting co-occurrence";
    default:
      return evidenceType.replace(/_/g, " ").toLowerCase();
  }
}

export function relationshipPhrase(relationshipType: string): string {
  switch (relationshipType) {
    case "DEPENDS_ON":
      return "depends on";
    case "BLOCKS":
      return "blocks";
    case "CO_OCCURS_WITH":
      return "observed with";
    case "RELATED_TO":
      return "related to";
    default:
      return relationshipType.replace(/_/g, " ").toLowerCase();
  }
}

const RISK_ORDER: Record<string, number> = { CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1 };

export function portfolioRiskRank(level: string | null | undefined): number {
  return level ? (RISK_ORDER[level] ?? 0) : 0;
}

export function combineEntityDirectory(
  entities: EntityResponse[],
  summaries: PortfolioEntitySummary[],
): EntityDirectoryRow[] {
  const byId = new Map(summaries.map((summary) => [summary.entity_id, summary]));
  return entities.map((entity) => ({ entity, summary: byId.get(entity.entity_id) ?? null }));
}

export function applyEntityDirectoryFilters(
  rows: EntityDirectoryRow[],
  filters: EntityDirectoryFilters,
): EntityDirectoryRow[] {
  const query = normalizeEntityQuery(filters.query);
  const selected = rows.filter((row) => {
    if (filters.type !== "ALL" && row.entity.entity_type !== filters.type) return false;
    if (filters.state !== "ALL" && row.summary?.current_state !== filters.state) return false;
    if (query && !entitySearchText(row.entity).includes(query)) return false;
    return true;
  });

  return [...selected].sort((first, second) => {
    if (filters.sort === "name") {
      return (
        first.entity.canonical_name.localeCompare(second.entity.canonical_name) ||
        first.entity.entity_id.localeCompare(second.entity.entity_id)
      );
    }
    if (filters.sort === "observations") {
      return (
        (second.summary?.observation_count ?? 0) - (first.summary?.observation_count ?? 0) ||
        first.entity.canonical_name.localeCompare(second.entity.canonical_name) ||
        first.entity.entity_id.localeCompare(second.entity.entity_id)
      );
    }
    const firstAssessed = first.summary ? 1 : 0;
    const secondAssessed = second.summary ? 1 : 0;
    return (
      secondAssessed - firstAssessed ||
      (second.summary?.attention_score ?? 0) - (first.summary?.attention_score ?? 0) ||
      portfolioRiskRank(second.summary?.risk_level) - portfolioRiskRank(first.summary?.risk_level) ||
      (second.summary?.impact_count ?? 0) - (first.summary?.impact_count ?? 0) ||
      (second.summary?.observation_count ?? 0) - (first.summary?.observation_count ?? 0) ||
      first.entity.canonical_name.localeCompare(second.entity.canonical_name) ||
      first.entity.entity_id.localeCompare(second.entity.entity_id)
    );
  });
}

export function formatEntityDateTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const time = Date.parse(iso);
  if (Number.isNaN(time)) return null;
  return new Date(time).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatEntityDay(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const time = Date.parse(iso);
  if (Number.isNaN(time)) return null;
  return new Date(time).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export interface EntityMentionedMeeting {
  meeting_id: string;
  title: string;
  meeting_date: string;
  observationCount: number;
}

export function selectEntityMeetingsFromTemporal(
  timeline: Array<{ meeting_id: string; meeting_title: string; meeting_date: string }> | undefined,
  limit = 8,
): EntityMentionedMeeting[] {
  const meetings = new Map<string, EntityMentionedMeeting>();
  for (const observation of timeline ?? []) {
    if (!observation.meeting_id) continue;
    const existing = meetings.get(observation.meeting_id);
    if (existing) {
      existing.observationCount += 1;
      continue;
    }
    meetings.set(observation.meeting_id, {
      meeting_id: observation.meeting_id,
      title: observation.meeting_title,
      meeting_date: observation.meeting_date,
      observationCount: 1,
    });
  }
  return [...meetings.values()]
    .sort((first, second) => {
      const firstTime = Date.parse(first.meeting_date);
      const secondTime = Date.parse(second.meeting_date);
      if (Number.isNaN(firstTime) && Number.isNaN(secondTime)) {
        return first.meeting_id.localeCompare(second.meeting_id);
      }
      if (Number.isNaN(firstTime)) return 1;
      if (Number.isNaN(secondTime)) return -1;
      return secondTime - firstTime || first.meeting_id.localeCompare(second.meeting_id);
    })
    .slice(0, Math.max(0, limit));
}

export type EntityConnectionRole =
  | "dependency"
  | "dependent"
  | "blocking"
  | "blocked-by"
  | "association";

export interface EntityConnection {
  relationship: EntityRelationship;
  role: EntityConnectionRole;
  otherEntityId: string | null;
  phrase: string;
}

export function describeEntityRelationship(
  relationship: EntityRelationship,
  entityId: string,
): EntityConnection {
  const other =
    relationship.source_entity_id === entityId
      ? relationship.target_entity_id
      : relationship.source_entity_id;
  if (relationship.relationship_type === "DEPENDS_ON") {
    if (relationship.source_entity_id === entityId) {
      return { relationship, role: "dependency", otherEntityId: other, phrase: "Depends on" };
    }
    return { relationship, role: "dependent", otherEntityId: other, phrase: "Required by" };
  }
  if (relationship.relationship_type === "BLOCKS") {
    if (relationship.source_entity_id === entityId) {
      return { relationship, role: "blocking", otherEntityId: other, phrase: "Blocks" };
    }
    return { relationship, role: "blocked-by", otherEntityId: other, phrase: "Blocked by" };
  }
  return {
    relationship,
    role: "association",
    otherEntityId: other,
    phrase:
      relationship.relationship_type === "CO_OCCURS_WITH" ? "Observed with" : "Related to",
  };
}

export function selectLatestInsight(insights: EntityInsight[] | undefined): EntityInsight | null {
  if (!insights || insights.length === 0) return null;
  return insights[insights.length - 1];
}

export function selectRiskSignals(insights: EntityInsight[] | undefined): EntityInsight[] {
  return (insights ?? []).filter(
    (insight) => insight.severity === "WARNING" || insight.severity === "CRITICAL",
  );
}

export function selectLatestTimelineEvent(events: TimelineEvent[] | undefined): TimelineEvent | null {
  if (!events || events.length === 0) return null;
  return events[events.length - 1];
}

export function describeAttention(attention: EntityAttention | null | undefined): string | null {
  if (!attention) return null;
  const reasons = attention.reasons.map(attentionReasonLabel).join(", ");
  return `${attentionLevelLabel(attention.attention_level)} · score ${attention.score}${
    reasons ? ` · ${reasons}` : ""
  }`;
}
