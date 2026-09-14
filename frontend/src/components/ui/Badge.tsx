import type { ReactNode } from "react";
import type { StatusTone } from "./status";

/* Badge: quiet pill for metadata. StatusBadge: tone + text label. */

export function Badge({
  children,
  tone = "neutral",
  className = "",
}: {
  children: ReactNode;
  tone?: StatusTone | "teal";
  className?: string;
}): React.JSX.Element {
  return <span className={["tl-badge", `tl-badge-${tone}`, className].filter(Boolean).join(" ")}>{children}</span>;
}

export function StatusBadge({
  tone,
  label,
  pulse = false,
  className = "",
}: {
  tone: StatusTone;
  label: string;
  pulse?: boolean;
  className?: string;
}): React.JSX.Element {
  return (
    <span
      className={["tl-badge", `tl-badge-${tone}`, "tl-status-badge", className]
        .filter(Boolean)
        .join(" ")}
    >
      <span
        className={["tl-status-dot", pulse ? "tl-status-dot-pulse" : ""].filter(Boolean).join(" ")}
        aria-hidden="true"
      />
      {label}
    </span>
  );
}
