import type { ReactNode } from "react";

/* Card: bordered surface for grouped content. Section: page rhythm wrapper
 * with an optional heading row. Not every section needs a Card. */

export function Card({
  children,
  className = "",
  padded = true,
}: {
  children: ReactNode;
  className?: string;
  padded?: boolean;
}): React.JSX.Element {
  return (
    <div className={["tl-card", padded ? "" : "tl-card-flush", className].filter(Boolean).join(" ")}>
      {children}
    </div>
  );
}

export function Section({
  title,
  description,
  actions,
  children,
  className = "",
}: {
  title?: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}): React.JSX.Element {
  return (
    <section className={["tl-section", className].filter(Boolean).join(" ")}>
      {title || actions ? (
        <div className="tl-section-head">
          <div>
            {title ? <h2 className="tl-section-title">{title}</h2> : null}
            {description ? <p className="tl-body-secondary">{description}</p> : null}
          </div>
          {actions ? <div className="tl-section-actions">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}
