import { useEffect, useId, useRef, useState, type ReactNode } from "react";

/* Tooltip: CSS-driven, appears on hover AND keyboard focus. */

export function Tooltip({
  label,
  children,
  position = "top",
}: {
  label: string;
  children: ReactNode;
  position?: "top" | "bottom" | "left" | "right";
}): React.JSX.Element {
  return (
    <span className={["tl-tooltip", `tl-tooltip-${position}`].join(" ")} data-tip={label} tabIndex={0}>
      {children}
    </span>
  );
}

/* Dropdown: accessible menu with Escape/outside-click dismissal. */

export interface DropdownItem {
  id: string;
  label: string;
  description?: string;
  danger?: boolean;
  disabled?: boolean;
}

export function Dropdown({
  trigger,
  items,
  onSelect,
  label,
  align = "end",
}: {
  trigger: ReactNode;
  items: DropdownItem[];
  onSelect: (id: string) => void;
  label: string;
  align?: "start" | "end";
}): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent): void {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open ]);

  return (
    <div className="tl-dropdown" ref={rootRef}>
      <div
        role="button"
        tabIndex={0}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        className="tl-dropdown-trigger"
        onClick={() => setOpen((value) => !value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            setOpen((value) => !value);
          }
        }}
      >
        {trigger}
      </div>
      {open ? (
        <div id={menuId} role="menu" aria-label={label} className={["tl-menu", `tl-menu-${align}`].join(" ")}>
          {items.map((item) => (
            <button
              key={item.id}
              role="menuitem"
              type="button"
              disabled={item.disabled}
              className={["tl-menu-item", item.danger ? "tl-menu-item-danger" : ""].join(" ")}
              onClick={() => {
                setOpen(false);
                onSelect(item.id);
              }}
            >
              <span className="tl-menu-label">{item.label}</span>
              {item.description ? <span className="tl-menu-desc">{item.description}</span> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
