/* ThreadDiagram — hero signature visual.
 * Meeting -> Observation -> Entity -> Memory -> Change -> Intelligence -> Evidence.
 * Staged cinematic entrance: edges draw in sequence, nodes resolve one by one,
 * then a barely-perceptible ambient shimmer remains. Fully static under
 * prefers-reduced-motion (CSS). Fine lines, small nodes, editorial labels.
 * Deep ink + muted teal + warm copper accent on the evidence node. */

import { useState } from "react";
import { useMediaQuery } from "./useLandingAnimations";

const NODES = [
  { id: "meeting", label: "Meeting", cx: 60, cy: 140 },
  { id: "observation", label: "Observation", cx: 172, cy: 78 },
  { id: "entity", label: "Entity", cx: 298, cy: 118 },
  { id: "memory", label: "Memory", cx: 422, cy: 66 },
  { id: "change", label: "Change", cx: 534, cy: 128 },
  { id: "intelligence", label: "Intelligence", cx: 652, cy: 72 },
  { id: "evidence", label: "Evidence", cx: 762, cy: 136 },
] as const;

/* Compact vertical arrangement for narrow viewports: same seven stages,
 * top to bottom, labels beside each node at a readable size. */
const COMPACT_NODES = [
  { id: "meeting", label: "Meeting", cx: 36, cy: 40 },
  { id: "observation", label: "Observation", cx: 36, cy: 120 },
  { id: "entity", label: "Entity", cx: 36, cy: 200 },
  { id: "memory", label: "Memory", cx: 36, cy: 280 },
  { id: "change", label: "Change", cx: 36, cy: 360 },
  { id: "intelligence", label: "Intelligence", cx: 36, cy: 440 },
  { id: "evidence", label: "Evidence", cx: 36, cy: 520 },
] as const;

const EDGES: [number, number][] = [
  [0, 1],
  [1, 2],
  [2, 3],
  [3, 4],
  [4, 5],
  [5, 6],
];

/** Gentle quadratic curve between two nodes for an intentional, hand-drawn feel. */
function edgePath(a: { cx: number; cy: number }, b: { cx: number; cy: number }): string {
  const mx = (a.cx + b.cx) / 2;
  const my = (a.cy + b.cy) / 2 - 14;
  return `M ${a.cx} ${a.cy} Q ${mx} ${my} ${b.cx} ${b.cy}`;
}

export function ThreadDiagram({ animate = true }: { animate?: boolean }): React.JSX.Element {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const compact = useMediaQuery("(max-width: 640px)");
  const nodes = compact ? COMPACT_NODES : NODES;

  function isConnected(idx: number): boolean {
    if (hoveredIdx === null) return false;
    return (
      idx === hoveredIdx ||
      EDGES.some(
        ([a, b]) =>
          (a === hoveredIdx && b === idx) || (b === hoveredIdx && a === idx),
      )
    );
  }

  function edgeConnected(a: number, b: number): boolean {
    if (hoveredIdx === null) return false;
    return hoveredIdx === a || hoveredIdx === b;
  }

  return (
    <svg
      className={`tl-thread-diagram ${animate ? "tl-thread-animate" : ""} ${compact ? "tl-thread-compact" : ""}`}
      viewBox={compact ? "0 0 220 570" : "0 0 820 210"}
      role="group"
      aria-label="ThreadLine information flow: Meeting to Observation to Entity to Memory to Change to Intelligence to Evidence"
      xmlns="http://www.w3.org/2000/svg"
    >
      {/* Ambient depth: two faint contour arcs behind the thread */}
      {!compact && (
        <g className="tl-thread-ambient" aria-hidden="true">
          <path d="M 30 190 Q 220 150 410 170 T 790 150" className="tl-thread-contour" />
          <path d="M 40 40 Q 260 80 470 45 T 780 70" className="tl-thread-contour" />
        </g>
      )}
      {/* Edges draw in sequence */}
      <g className="tl-thread-edges">
        {EDGES.map(([a, b], i) => (
          <path
            key={`e${i}`}
            d={edgePath(nodes[a], nodes[b])}
            className={`tl-thread-edge ${edgeConnected(a, b) ? "tl-thread-edge-active" : ""}`}
            style={{ animationDelay: animate ? `${0.55 + i * 0.32}s` : undefined }}
          />
        ))}
      </g>
      {/* Nodes resolve one by one. Labels stay fully opaque from frame one
          (axe-deterministic contrast); only dots and halos animate in. */}
      <g className="tl-thread-nodes">
        {nodes.map((node, i) => (
          <g
            key={node.id}
            className={`tl-thread-node ${isConnected(i) ? "tl-thread-node-active" : ""} ${node.id === "evidence" ? "tl-thread-node-accent" : ""}`}
            style={
              { "--tl-node-delay": animate ? `${0.4 + i * 0.3}s` : "0s" } as React.CSSProperties
            }
            onMouseEnter={() => setHoveredIdx(i)}
            onMouseLeave={() => setHoveredIdx(null)}
            onFocus={() => setHoveredIdx(i)}
            onBlur={() => setHoveredIdx(null)}
            tabIndex={0}
            role="button"
            aria-label={node.label}
          >
            <circle cx={node.cx} cy={node.cy} r="14" className="tl-thread-halo" />
            <circle cx={node.cx} cy={node.cy} r="6" className="tl-thread-dot" />
            {compact ? (
              <text x={node.cx + 24} y={node.cy + 4.5} className="tl-thread-label" textAnchor="start">
                {node.label}
              </text>
            ) : (
              <text
                x={node.cx}
                y={node.cy + 30}
                className="tl-thread-label"
                textAnchor="middle"
              >
                {node.label}
              </text>
            )}
          </g>
        ))}
      </g>
    </svg>
  );
}
