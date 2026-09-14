import { useId, type ButtonHTMLAttributes } from "react";

/* Tabs: roving selection with real tablist semantics and keyboard arrows. */

export interface TabItem {
  id: string;
  label: string;
  disabled?: boolean;
}

export function Tabs({
  items,
  value,
  onChange,
  label,
}: {
  items: TabItem[];
  value: string;
  onChange: (id: string) => void;
  label: string;
}): React.JSX.Element {
  const baseId = useId();
  return (
    <div
      role="tablist"
      aria-label={label}
      className="tl-tabs"
      onKeyDown={(event) => {
        if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
        event.preventDefault();
        const enabled = items.filter((item) => !item.disabled);
        const current = enabled.findIndex((item) => item.id === value);
        const delta = event.key === "ArrowRight" ? 1 : -1;
        const next = enabled[(current + delta + enabled.length) % enabled.length];
        if (next) {
          onChange(next.id);
          document.getElementById(`${baseId}-${next.id}`)?.focus();
        }
      }}
    >
      {items.map((item) => {
        const selected = item.id === value;
        return (
          <button
            key={item.id}
            id={`${baseId}-${item.id}`}
            role="tab"
            type="button"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            disabled={item.disabled}
            className={["tl-tab", selected ? "tl-tab-selected" : ""].join(" ")}
            onClick={() => onChange(item.id)}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

/* Pagination: compact prev/next + numbered window for server lists. */

export function Pagination({
  page,
  pageCount,
  onChange,
  label = "Pagination",
}: {
  page: number;
  pageCount: number;
  onChange: (page: number) => void;
  label?: string;
}): React.JSX.Element | null {
  if (pageCount <= 1) return null;
  const window = 2;
  const pages: number[] = [];
  for (let p = 1; p <= pageCount; p += 1) {
    if (p === 1 || p === pageCount || Math.abs(p - page) <= window) pages.push(p);
  }
  const items: (number | "gap")[] = [];
  pages.forEach((p, index) => {
    if (index > 0 && p - (pages[index - 1] as number) > 1) items.push("gap");
    items.push(p);
  });
  const buttonProps = (
    target: number,
    extra: ButtonHTMLAttributes<HTMLButtonElement> = {},
  ): ButtonHTMLAttributes<HTMLButtonElement> => ({
    type: "button",
    disabled: target < 1 || target > pageCount || target === page,
    onClick: () => onChange(target),
    ...extra,
  });
  return (
    <nav className="tl-pagination" aria-label={label}>
      <button className="tl-page-btn" aria-label="Previous page" {...buttonProps(page - 1)}>
        ‹
      </button>
      {items.map((item, index) =>
        item === "gap" ? (
          <span key={`gap-${index}`} className="tl-page-gap" aria-hidden="true">
            …
          </span>
        ) : (
          <button
            key={item}
            className={["tl-page-btn", item === page ? "tl-page-current" : ""].join(" ")}
            aria-label={`Page ${item}`}
            aria-current={item === page ? "page" : undefined}
            {...buttonProps(item)}
          >
            {item}
          </button>
        ),
      )}
      <button className="tl-page-btn" aria-label="Next page" {...buttonProps(page + 1)}>
        ›
      </button>
    </nav>
  );
}
