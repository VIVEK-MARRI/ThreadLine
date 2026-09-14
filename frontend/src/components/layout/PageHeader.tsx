import type { ReactNode } from "react";
import { Breadcrumbs, type Crumb } from "../ui/Breadcrumbs";

/* PageHeader: the consistent title region for every app screen —
 * breadcrumbs, title, description, and a right-aligned action cluster. */

export function PageHeader({
  title,
  description,
  crumbs = [],
  actions,
}: {
  title: string;
  description?: string;
  crumbs?: Crumb[];
  actions?: ReactNode;
}): React.JSX.Element {
  return (
    <div className="tl-page-head">
      {crumbs.length > 0 ? <Breadcrumbs items={crumbs} /> : null}
      <div className="tl-page-head-row">
        <div className="tl-page-head-text">
          <h1 className="tl-page-title">{title}</h1>
          {description ? <p className="tl-page-desc">{description}</p> : null}
        </div>
        {actions ? <div className="tl-page-actions">{actions}</div> : null}
      </div>
    </div>
  );
}
