/* Avatar: initials in a warm neutral circle. No photos in the foundation. */

const PALETTE = [
  ["#e8dfd2", "#5c564a"],
  ["#dce7f3", "#274b77"],
  ["#d9ebe4", "#1d5a4e"],
  ["#f0e3cf", "#8a5a12"],
  ["#e7d5d0", "#8c3a2a"],
] as const;

function initialsFor(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return (parts[0] as string).slice(0, 2).toUpperCase();
  return `${(parts[0] as string)[0]}${(parts[parts.length - 1] as string)[0]}`.toUpperCase();
}

function paletteFor(name: string): readonly [string, string] {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return PALETTE[hash % PALETTE.length] as readonly [string, string];
}

export function Avatar({
  name,
  size = "md",
  label,
}: {
  name: string;
  size?: "sm" | "md" | "lg";
  label?: string;
}): React.JSX.Element {
  const [background, color] = paletteFor(name || "?");
  return (
    <span
      className={["tl-avatar", `tl-avatar-${size}`].join(" ")}
      style={{ background, color }}
      role="img"
      aria-label={label ?? name}
      title={name}
    >
      {initialsFor(name)}
    </span>
  );
}
