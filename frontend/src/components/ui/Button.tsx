import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  fullWidth?: boolean;
  children: ReactNode;
}

/** Primary product button. Loading swaps content for a spinner + keeps width. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "secondary",
    size = "md",
    loading = false,
    fullWidth = false,
    disabled,
    className = "",
    children,
    type = "button",
    ...rest
  },
  ref,
) {
  const classes = [
    "tl-btn",
    `tl-btn-${variant}`,
    `tl-btn-${size}`,
    loading ? "tl-btn-loading" : "",
    fullWidth ? "tl-btn-full" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled ?? loading}
      aria-busy={loading || undefined}
      className={classes}
      {...rest}
    >
      {loading ? <span className="tl-spinner tl-spinner-inline" aria-hidden="true" /> : null}
      <span className="tl-btn-label">{children}</span>
    </button>
  );
});

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  variant?: "ghost" | "secondary";
  size?: ButtonSize;
  children: ReactNode;
}

/** Icon-only button. `label` is required and becomes the accessible name. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  function IconButton({ label, variant = "ghost", size = "md", className = "", ...rest }, ref) {
    return (
      <button
        ref={ref}
        type="button"
        aria-label={label}
        title={label}
        className={["tl-icon-btn", `tl-icon-btn-${variant}`, `tl-icon-btn-${size}`, className]
          .filter(Boolean)
          .join(" ")}
        {...rest}
      />
    );
  },
);
