/* ThreadLine wordmark: navy tile + set-in-cap wordmark. No gradients. */

export function Logo({ compact = false }: { compact?: boolean }): React.JSX.Element {
  return (
    <span className={["tl-logo", compact ? "tl-logo-compact" : ""].join(" ")} aria-label="ThreadLine">
      <svg className="tl-logo-mark" viewBox="0 0 32 32" aria-hidden="true">
        <rect width="32" height="32" rx="7" fill="#1e3a5f" />
        <path d="M9 8h14M16 8v16" stroke="#f5f1e8" strokeWidth="2.6" strokeLinecap="round" />
        <circle cx="16" cy="24" r="2.2" fill="#7fb3a3" />
      </svg>
      {!compact ? <span className="tl-logo-word">ThreadLine</span> : null}
    </span>
  );
}
