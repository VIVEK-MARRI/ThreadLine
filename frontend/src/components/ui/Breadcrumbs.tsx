import { Link } from "react-router-dom";

/* Breadcrumbs: quiet path trail above the page title. */

export interface Crumb {
  label: string;
  to?: string;
}

export function Breadcrumbs({ items, label = "Breadcrumb" }: { items: Crumb[]; label?: string }): React.JSX.Element | null {
  if (items.length === 0) return null;
  return (
    <nav className="tl-breadcrumbs" aria-label={label}>
      <ol>
        {items.map((item, index) => {
          const last = index === items.length - 1;
          return (
            <li key={`${item.label}-${index}`}>
              {index > 0 ? (
                <span className="tl-crumb-sep" aria-hidden="true">
                  /
                </span>
              ) : null}
              {last || !item.to ? (
                <span aria-current={last ? "page" : undefined} className={last ? "tl-crumb-current" : undefined}>
                  {item.label}
                </span>
              ) : (
                <Link to={item.to}>{item.label}</Link>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
