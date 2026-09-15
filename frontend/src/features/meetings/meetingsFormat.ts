/* Meeting workspace presentation helpers.
 *
 * Pure mappings of backend-controlled meeting vocabularies and real
 * timestamps to readable copy. No thresholds, classification, or invented
 * relationships live here.
 */

import type { StatusTone } from "../../components/ui/status";
import type {
  ExtractionResponse,
  MeetingMention,
  MeetingProcessingStatus,
  MeetingSummary,
} from "../../types/meetings";

export const MEETING_LIST_LIMIT_OPTIONS = [25, 50, 100, 200] as const;
export const DEFAULT_MEETING_LIST_LIMIT = 50;

export type MeetingListStatusFilter = "ALL" | MeetingProcessingStatus;
export type MeetingListSort = "newest" | "oldest";

export interface MeetingListFilters {
  query: string;
  status: MeetingListStatusFilter;
  fromDate: string;
  toDate: string;
  sort: MeetingListSort;
}

export function parseMeetingListLimit(value: string | null): number {
  const parsed = Number(value);
  if (MEETING_LIST_LIMIT_OPTIONS.includes(parsed as (typeof MEETING_LIST_LIMIT_OPTIONS)[number])) {
    return parsed;
  }
  return DEFAULT_MEETING_LIST_LIMIT;
}

export function parseMeetingStatusFilter(value: string | null): MeetingListStatusFilter {
  if (
    value === "CURRENT" ||
    value === "PENDING" ||
    value === "INCOMPLETE" ||
    value === "FAILED" ||
    value === "STALE"
  ) {
    return value;
  }
  return "ALL";
}

export function parseMeetingSort(value: string | null): MeetingListSort {
  return value === "oldest" ? "oldest" : "newest";
}

export function formatMeetingDateTime(iso: string | null | undefined): string | null {
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

export function formatMeetingDay(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const time = Date.parse(iso);
  if (Number.isNaN(time)) return null;
  return new Date(time).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function relativeMeetingTime(iso: string | null | undefined, now: number = Date.now()): string | null {
  if (!iso) return null;
  const time = Date.parse(iso);
  if (Number.isNaN(time)) return null;
  const seconds = Math.max(0, Math.floor((now - time) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

export function processingStatusLabel(status: MeetingProcessingStatus): string {
  switch (status) {
    case "CURRENT":
      return "Complete";
    case "PENDING":
      return "Processing";
    case "INCOMPLETE":
      return "Awaiting processing";
    case "FAILED":
      return "Failed";
    case "STALE":
      return "Needs refresh";
  }
}

export function processingStatusTone(status: MeetingProcessingStatus): StatusTone {
  switch (status) {
    case "CURRENT":
      return "success";
    case "PENDING":
      return "info";
    case "INCOMPLETE":
      return "neutral";
    case "FAILED":
      return "danger";
    case "STALE":
      return "warning";
  }
}

/** Backend extraction severities are free text: tone only known levels. */
export function extractionRiskTone(severity: string | null | undefined): StatusTone {
  switch (severity?.trim().toLowerCase()) {
    case "critical":
      return "danger";
    case "high":
      return "warning";
    case "medium":
      return "info";
    case "low":
      return "neutral";
    default:
      return "neutral";
  }
}

export function riskSeverityLabel(severity: string | null | undefined): string {
  const trimmed = severity?.trim();
  return trimmed ? trimmed : "Severity not stated";
}

export function participantPreview(participants: string[], maximum = 3): { shown: string[]; remaining: number } {
  const shown = participants.slice(0, Math.max(0, maximum));
  return { shown, remaining: Math.max(0, participants.length - shown.length) };
}

export function extractedFactSummary(extraction: ExtractionResponse | null): string {
  if (!extraction) return "Not extracted yet";
  const parts: string[] = [];
  if (extraction.decisions.length > 0) {
    parts.push(`${extraction.decisions.length} ${extraction.decisions.length === 1 ? "decision" : "decisions"}`);
  }
  if (extraction.tasks.length > 0) {
    parts.push(`${extraction.tasks.length} ${extraction.tasks.length === 1 ? "task" : "tasks"}`);
  }
  if (extraction.issues.length > 0) {
    parts.push(`${extraction.issues.length} ${extraction.issues.length === 1 ? "issue" : "issues"}`);
  }
  if (extraction.risks.length > 0) {
    parts.push(`${extraction.risks.length} ${extraction.risks.length === 1 ? "risk" : "risks"}`);
  }
  return parts.length > 0 ? parts.join(" · ") : "Extracted · no facts";
}

export function listFactSummary(summary: MeetingSummary): string {
  if (summary.extracted_at === null) return "Not extracted yet";
  const parts: string[] = [];
  if (summary.decision_count > 0) {
    parts.push(`${summary.decision_count} ${summary.decision_count === 1 ? "decision" : "decisions"}`);
  }
  if (summary.task_count > 0) {
    parts.push(`${summary.task_count} ${summary.task_count === 1 ? "task" : "tasks"}`);
  }
  if (summary.issue_count > 0) {
    parts.push(`${summary.issue_count} ${summary.issue_count === 1 ? "issue" : "issues"}`);
  }
  if (summary.risk_count > 0) {
    parts.push(`${summary.risk_count} ${summary.risk_count === 1 ? "risk" : "risks"}`);
  }
  return parts.length > 0 ? parts.join(" · ") : "Extracted · no facts";
}

export function linkedEntitySummary(count: number): string {
  if (count <= 0) return "No linked entities";
  return `${count} ${count === 1 ? "linked entity" : "linked entities"}`;
}

export function meetingSearchText(meeting: MeetingSummary): string {
  return [meeting.title, meeting.meeting_id, ...meeting.participants].join("\n").toLowerCase();
}

export function isWithinMeetingDayRange(meetingDate: string, fromDate: string, toDate: string): boolean {
  const time = Date.parse(meetingDate);
  if (Number.isNaN(time)) return false;
  if (fromDate) {
    const from = Date.parse(`${fromDate}T00:00:00`);
    if (!Number.isNaN(from) && time < from) return false;
  }
  if (toDate) {
    const to = Date.parse(`${toDate}T23:59:59.999`);
    if (!Number.isNaN(to) && time > to) return false;
  }
  return true;
}

export function applyLoadedMeetingFilters(
  meetings: MeetingSummary[],
  filters: MeetingListFilters,
): MeetingSummary[] {
  const query = filters.query.trim().toLowerCase();
  const selected = meetings.filter((meeting) => {
    if (filters.status !== "ALL" && meeting.processing_status !== filters.status) return false;
    if (!isWithinMeetingDayRange(meeting.meeting_date, filters.fromDate, filters.toDate)) return false;
    if (query && !meetingSearchText(meeting).includes(query)) return false;
    return true;
  });
  return [...selected].sort((first, second) => {
    const firstTime = Date.parse(first.meeting_date);
    const secondTime = Date.parse(second.meeting_date);
    if (filters.sort === "oldest") {
      return firstTime - secondTime || first.meeting_id.localeCompare(second.meeting_id);
    }
    return secondTime - firstTime || first.meeting_id.localeCompare(second.meeting_id);
  });
}

export function hasActiveLoadedMeetingFilters(filters: MeetingListFilters): boolean {
  return (
    filters.query.trim() !== "" ||
    filters.status !== "ALL" ||
    filters.fromDate !== "" ||
    filters.toDate !== "" ||
    filters.sort !== "newest"
  );
}

export function orderedMeetingMentions(mentions: MeetingMention[]): MeetingMention[] {
  return [...mentions].sort((first, second) => {
    if (first.source_revision !== second.source_revision) {
      return first.source_revision - second.source_revision;
    }
    return first.mention_id.localeCompare(second.mention_id);
  });
}

export function uniqueResolvedEntityIds(mentions: MeetingMention[]): string[] {
  return Array.from(
    new Set(
      mentions
        .filter((mention) => mention.entity_id !== null && mention.resolution_status === "RESOLVED")
        .map((mention) => mention.entity_id as string),
    ),
  ).sort();
}

export function transcriptSizeLabel(transcript: string): string {
  return `${transcript.length.toLocaleString()} characters`;
}
