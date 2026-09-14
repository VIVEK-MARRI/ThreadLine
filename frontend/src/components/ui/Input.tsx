import {
  cloneElement,
  forwardRef,
  isValidElement,
  type InputHTMLAttributes,
  type ReactElement,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

/* Field: label + control + hint/error wiring with correct htmlFor/ids. */

export interface FieldProps {
  label: string;
  htmlFor: string;
  hint?: string;
  error?: string | null;
  required?: boolean;
  children: ReactNode;
}

export function Field({ label, htmlFor, hint, error, required, children }: FieldProps): React.JSX.Element {
  const hintId = hint ? `${htmlFor}-hint` : undefined;
  const errorId = error ? `${htmlFor}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
  // Wire the described-by ids onto a single child control when possible so
  // assistive tech announces hints and errors with the field.
  let control: ReactNode = children;
  if (describedBy && isValidElement(children)) {
    const element = children as ReactElement<{ "aria-describedby"?: string }>;
    const existing = element.props["aria-describedby"];
    control = cloneElement(element, {
      "aria-describedby": existing ? `${existing} ${describedBy}` : describedBy,
    });
  }
  return (
    <div className="tl-field">
      <label className="tl-label" htmlFor={htmlFor}>
        {label}
        {required ? (
          <span className="tl-required" aria-hidden="true">
            {" "}
            *
          </span>
        ) : null}
      </label>
      {control}
      {hint && !error ? (
        <p className="tl-hint" id={hintId}>
          {hint}
        </p>
      ) : null}
      {error ? (
        <p className="tl-error-text" id={errorId} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { invalid, className = "", ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      aria-invalid={invalid || undefined}
      className={["tl-input", invalid ? "tl-input-invalid" : "", className]
        .filter(Boolean)
        .join(" ")}
      {...rest}
    />
  );
});

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { invalid, className = "", rows = 4, ...rest },
  ref,
) {
  return (
    <textarea
      ref={ref}
      rows={rows}
      aria-invalid={invalid || undefined}
      className={["tl-input", "tl-textarea", invalid ? "tl-input-invalid" : "", className]
        .filter(Boolean)
        .join(" ")}
      {...rest}
    />
  );
});

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  invalid?: boolean;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { invalid, className = "", children, ...rest },
  ref,
) {
  return (
    <span className="tl-select-wrap">
      <select
        ref={ref}
        aria-invalid={invalid || undefined}
        className={["tl-input", "tl-select", invalid ? "tl-input-invalid" : "", className]
          .filter(Boolean)
          .join(" ")}
        {...rest}
      >
        {children}
      </select>
    </span>
  );
});

export interface CheckboxProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  description?: string;
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { label, description, className = "", id, ...rest },
  ref,
) {
  const controlId = id ?? `tl-checkbox-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <div className={["tl-check-row", className].filter(Boolean).join(" ")}>
      <input ref={ref} id={controlId} type="checkbox" className="tl-check" {...rest} />
      <div className="tl-check-text">
        <label className="tl-check-label" htmlFor={controlId}>
          {label}
        </label>
        {description ? <p className="tl-hint">{description}</p> : null}
      </div>
    </div>
  );
});
