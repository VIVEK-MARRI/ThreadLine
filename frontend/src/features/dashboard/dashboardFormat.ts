/* Dashboard presentation helpers.
 *
 * Pure mappings of backend-controlled vocabularies to human copy, plus
 * time formatting over real backend timestamps. No thresholds, no levels,
 * no risk derivation here — ordering and classification always come from
 * the API. Unknown wire values degrade to readable fallbacks, never crash.
 */

export function greetingFor(date: Date = new Date()): string {
  const hour = date.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

/** Display name from the user's own identifier (email local part). */
export function displayNameFromEmail(email: string | null | undefined): string | null {
  if (!email) return null;
  const local = email.split("@")[0] ?? "";
  const first = local.split(/[._-]+/).filter(Boolean)[0] ?? "";
  if (!first) return null;
  return first.charAt(0).toUpperCase() + first.slice(1);
}

/** "5m ago", "3h ago", "2d ago" — null when the timestamp is missing/invalid. */
export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string | null {
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

export function formatDateTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const time = Date.parse(iso);
  if (Number.isNaN(time)) return null;
  return new Date(time).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** "1h 5m", "45s" — for queue-wait durations. Null when unavailable. */
export function formatDuration(totalSeconds: number | null | undefined): string | null {
  if (totalSeconds === null || totalSeconds === undefined || Number.isNaN(totalSeconds)) return null;
  const seconds = Math.max(0, Math.floor(totalSeconds));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder === 0 ? `${hours}h` : `${hours}h ${remainder}m`;
}

/* Backend attention reasons (AttentionReasonSchema) → human copy. */
const ATTENTION_REASONS: Record<string, string> = {
  ENTITY_BLOCKED: "Blocked",
  REOPEN_ATTEMPT: "Reopen attempt on a resolved item",
  ENTITY_STALE: "Not observed for a while",
  RECENT_STATE_CHANGE: "Recently changed state",
  REPEATED_OBSERVATION: "Raised repeatedly without progress",
  UNKNOWN_STATE: "Observed but state unclear",
};

export function attentionReasonLabel(reason: string): string {
  return ATTENTION_REASONS[reason] ?? reason.replace(/_/g, " ").toLowerCase();
}

/* Backend change types (OrganisationChangeTypeSchema) → human copy. */
const CHANGE_TYPES: Record<string, string> = {
  STATE_OPENED: "Opened",
  STATE_STARTED: "Started",
  STATE_BLOCKED: "Became blocked",
  STATE_RESOLVED: "Resolved",
  STATE_REGRESSED: "Regressed",
  STATE_REOPENED: "Reopened",
  REPEATED_UNRESOLVED: "Repeatedly unresolved",
  RISK_ESCALATED: "Risk escalated",
  RISK_DEESCALATED: "Risk cleared",
  NEW_DEPENDENCY: "New dependency",
  DEPENDENCY_EXPANDED: "Dependency chain grew",
  IMPACT_EXPANDED: "Impact widened",
  ENTITY_BECAME_STALE: "Went stale",
};

export function changeTypeLabel(changeType: string): string {
  return CHANGE_TYPES[changeType] ?? changeType.replace(/_/g, " ").toLowerCase();
}

/* Backend lifecycle states → human copy. */
const LIFECYCLE_STATES: Record<string, string> = {
  UNKNOWN: "Unknown",
  OPEN: "Open",
  IN_PROGRESS: "In progress",
  BLOCKED: "Blocked",
  RESOLVED: "Resolved",
};

export function lifecycleStateLabel(state: string | null | undefined): string {
  if (!state) return "Unknown";
  return LIFECYCLE_STATES[state] ?? state.replace(/_/g, " ").toLowerCase();
}

/** Short readable fallback when only a raw id is available. */
export function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}

/**
 * Compact one-line organisation snapshot from real aggregate counts.
 * Text, not stat cards — e.g. "12 entities · 2 need attention · 1 blocked".
 */
export function snapshotLine(input: {
  totalEntities: number;
  attentionCount: number;
  blockedCount: number;
}): string {
  const parts = [
    `${input.totalEntities} ${input.totalEntities === 1 ? "entity" : "entities"}`,
  ];
  if (input.attentionCount > 0) {
    parts.push(
      `${input.attentionCount} ${input.attentionCount === 1 ? "needs" : "need"} attention`,
    );
  }
  if (input.blockedCount > 0) {
    parts.push(`${input.blockedCount} blocked`);
  }
  return parts.join(" · ");
}
