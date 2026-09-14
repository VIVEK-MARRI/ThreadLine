/* ThreadLine API error model.
 *
 * Parses backend failures into a typed discriminated union so UI code can
 * branch on status instead of string-matching messages. Backend `detail`
 * strings are treated as untrusted display hints, never as logic.
 */

export type ApiErrorKind =
  | "unauthorized" // 401 — missing/invalid/expired session
  | "forbidden" // 403 — authenticated but not permitted
  | "not-found" // 404 — missing or outside tenant scope
  | "validation" // 422 — request schema violation
  | "conflict" // 409 — duplicate / ordering conflict
  | "rate-limited" // 429 — brute-force throttle
  | "unavailable" // 502/503 — provider or service unavailable
  | "network" // fetch itself failed (offline, refused, timeout)
  | "unknown";

export interface ApiErrorDetails {
  kind: ApiErrorKind;
  status: number | null;
  /** Backend `detail` or a safe fallback. Never raw exception text. */
  message: string;
  /** Field-level validation problems (422), when the backend provides them. */
  fields?: Record<string, string[]>;
}

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly fields?: Record<string, string[]>;

  constructor(details: ApiErrorDetails) {
    super(details.message);
    this.name = "ApiError";
    this.kind = details.kind;
    this.status = details.status;
    this.fields = details.fields;
  }
}

/** Friendly, non-technical message for end users. No raw errors leak. */
export function userFacingMessage(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.kind) {
      case "unauthorized":
        return "Your session has expired. Please sign in again.";
      case "forbidden":
        return "You don't have permission to do that.";
      case "not-found":
        return "We couldn't find what you were looking for.";
      case "validation":
        return error.message || "Please check the highlighted fields and try again.";
      case "conflict":
        return error.message || "That conflicts with something that already exists.";
      case "rate-limited":
        return "Too many attempts. Please wait a little while and try again.";
      case "unavailable":
        return "ThreadLine is temporarily unavailable. Please try again shortly.";
      case "network":
        return "Couldn't reach ThreadLine. Check your connection and try again.";
      default:
        return "Something went wrong. Please try again.";
    }
  }
  return "Something went wrong. Please try again.";
}
