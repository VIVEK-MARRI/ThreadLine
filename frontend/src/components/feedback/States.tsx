import type { ReactNode } from "react";
import { userFacingMessage } from "../../types/api";

/* The three product states every feature screen must handle.
 * Skeletons mirror the final layout; empty states explain + offer a next
 * action; errors explain + offer retry. Backend exception text never shows.
 */

export function Spinner({
  label = "Loading",
  size = "md",
}: {
  label?: string;
  size?: "sm" | "md" | "lg";
}): React.JSX.Element {
  return (
    <span role="status" aria-label={label} className="tl-spinner-wrap">
      <span className={["tl-spinner", `tl-spinner-${size}`].join(" ")} aria-hidden="true" />
      <span className="tl-sr-only">{label}</span>
    </span>
  );
}

export function Skeleton({
  lines = 3,
  label = "Loading content",
}: {
  lines?: number;
  label?: string;
}): React.JSX.Element {
  return (
    <div className="tl-skeleton" role="status" aria-label={label}>
      {Array.from({ length: lines }, (_, index) => (
        <span
          key={index}
          className="tl-skeleton-line"
          style={{ width: `${[96, 82, 91, 68, 77][index % 5]}%` }}
          aria-hidden="true"
        />
      ))}
    </div>
  );
}

export function LoadingState({
  title = "Loading",
  children,
}: {
  title?: string;
  children?: ReactNode;
}): React.JSX.Element {
  return (
    <div className="tl-state" role="status" aria-live="polite" aria-label={title}>
      <Spinner label={title} />
      <p className="tl-body-secondary">{children ?? "Gathering the latest from your organisation…"}</p>
    </div>
  );
}

export function EmptyState({
  title,
  body,
  action,
  icon,
}: {
  title: string;
  body: string;
  action?: ReactNode;
  icon?: ReactNode;
}): React.JSX.Element {
  return (
    <div className="tl-state">
      {icon ? (
        <span className="tl-state-icon" aria-hidden="true">
          {icon}
        </span>
      ) : null}
      <h2 className="tl-section-title">{title}</h2>
      <p className="tl-body-secondary">{body}</p>
      {action ? <div className="tl-state-action">{action}</div> : null}
    </div>
  );
}

export function ErrorState({
  title = "Something went wrong",
  error,
  onRetry,
  retryLabel = "Try again",
}: {
  title?: string;
  error: unknown;
  onRetry?: () => void;
  retryLabel?: string;
}): React.JSX.Element {
  return (
    <div className="tl-state" role="alert">
      <h2 className="tl-section-title">{title}</h2>
      <p className="tl-body-secondary">{userFacingMessage(error)}</p>
      {onRetry ? (
        <div className="tl-state-action">
          <button type="button" className="tl-btn tl-btn-secondary tl-btn-sm" onClick={onRetry}>
            {retryLabel}
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function ProgressIndicator({
  value,
  max = 100,
  label,
}: {
  value: number;
  max?: number;
  label: string;
}): React.JSX.Element {
  const percent = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="tl-progress">
      <div
        className="tl-progress-bar"
        role="progressbar"
        aria-label={label}
        aria-valuenow={Math.round(percent)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <span className="tl-progress-fill" style={{ width: `${percent}%` }} aria-hidden="true" />
      </div>
    </div>
  );
}
