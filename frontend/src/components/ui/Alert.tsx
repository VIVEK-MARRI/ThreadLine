import type { ReactNode } from "react";

/* Alert: inline, persistent, role-appropriate messaging for page context.
 * For fetch failures prefer <ErrorState> (with retry); Alert suits
 * explanations, warnings, and confirmations inside content. */

export type AlertTone = "info" | "success" | "warning" | "danger" | "neutral";

const ROLE_FOR_TONE: Record<AlertTone, "status" | "alert"> = {
  info: "status",
  success: "status",
  neutral: "status",
  warning: "alert",
  danger: "alert",
};

export function Alert({
  tone = "info",
  title,
  children,
  className = "",
}: {
  tone?: AlertTone;
  title?: string;
  children: ReactNode;
  className?: string;
}): React.JSX.Element {
  return (
    <div role={ROLE_FOR_TONE[tone]} className={["tl-alert", `tl-alert-${tone}`, className].filter(Boolean).join(" ")}>
      {title ? <p className="tl-alert-title">{title}</p> : null}
      <div className="tl-alert-body">{children}</div>
    </div>
  );
}
