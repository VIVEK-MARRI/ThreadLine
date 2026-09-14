/* ThreadLine status language — one visual treatment per semantic state.
 *
 * Tones are UI-only hints. Meaning is always carried by text as well
 * (never color alone); see StatusBadge which pairs tone with a label.
 */

export type StatusTone = "neutral" | "info" | "success" | "warning" | "danger";

export type MeetingProcessingState = "queued" | "processing" | "complete" | "failed";
export type EntityState = "resolved" | "ambiguous" | "unresolved";
export type RiskState = "low" | "medium" | "high" | "critical";
export type ActionState = "pending" | "in progress" | "completed";
export type JobState =
  | "pending"
  | "running"
  | "retrying"
  | "succeeded"
  | "failed"
  | "cancelled";

export function meetingStatusTone(state: MeetingProcessingState): StatusTone {
  switch (state) {
    case "queued":
      return "neutral";
    case "processing":
      return "info";
    case "complete":
      return "success";
    case "failed":
      return "danger";
  }
}

export function entityStatusTone(state: EntityState): StatusTone {
  switch (state) {
    case "resolved":
      return "success";
    case "ambiguous":
      return "warning";
    case "unresolved":
      return "neutral";
  }
}

export function riskTone(state: RiskState): StatusTone {
  switch (state) {
    case "low":
      return "neutral";
    case "medium":
      return "info";
    case "high":
      return "warning";
    case "critical":
      return "danger";
  }
}

/** Backend attention levels (UPPERCASE wire values) → tone. */
export function attentionLevelTone(level: string): StatusTone {
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

export function actionTone(state: ActionState): StatusTone {
  switch (state) {
    case "pending":
      return "neutral";
    case "in progress":
      return "info";
    case "completed":
      return "success";
  }
}

/** Backend job statuses → product job states. */
export function backendJobTone(status: string): StatusTone {
  switch (status) {
    case "SUCCEEDED":
      return "success";
    case "RUNNING":
      return "info";
    case "RETRY_WAITING":
      return "warning";
    case "FAILED":
      return "danger";
    default:
      return "neutral";
  }
}

export function jobStateTone(state: JobState): StatusTone {
  switch (state) {
    case "pending":
      return "neutral";
    case "running":
      return "info";
    case "retrying":
      return "warning";
    case "succeeded":
      return "success";
    case "failed":
      return "danger";
    case "cancelled":
      return "neutral";
  }
}
