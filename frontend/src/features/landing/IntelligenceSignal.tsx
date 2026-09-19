/* IntelligenceSignal — proactive scan visual.
 * A quiet field of stable organisation nodes; one newly relevant signal
 * gently emerges and connects by thread to its entity and evidence.
 * The scan sweep is CSS-animated and fully static under reduced motion.
 * No claims of real-time monitoring, prediction, or alerting. */

import { LANDING } from "./landingContent";
import { useReveal } from "./useLandingAnimations";

const STABLE_NODES = [
  { cx: 60, cy: 60 },
  { cx: 130, cy: 110 },
  { cx: 200, cy: 55 },
  { cx: 270, cy: 115 },
  { cx: 340, cy: 60 },
  { cx: 410, cy: 110 },
  { cx: 470, cy: 60 },
  { cx: 95, cy: 155 },
  { cx: 235, cy: 160 },
  { cx: 375, cy: 158 },
  { cx: 445, cy: 155 },
] as const;

const SIGNAL = { cx: 305, cy: 105 };

export function IntelligenceSignal(): React.JSX.Element {
  const { ref, revealed } = useReveal(0.25);

  return (
    <div
      ref={ref as React.RefObject<HTMLDivElement>}
      className={`tl-signal-scan ${revealed ? "tl-revealed" : ""}`}
    >
      <svg
        className="tl-signal-svg"
        viewBox="0 0 520 210"
        role="img"
        aria-label={LANDING.proactive.scanLabel}
        xmlns="http://www.w3.org/2000/svg"
      >
        {/* Stable organisation field */}
        <g className="tl-signal-stable" aria-hidden="true">
          {STABLE_NODES.map((n, i) => (
            <circle key={i} cx={n.cx} cy={n.cy} r="5" className="tl-signal-dot" />
          ))}
          <path
            d="M 60 60 Q 130 90 200 55 T 340 60 T 470 60"
            className="tl-signal-faint-line"
          />
          <path
            d="M 95 155 Q 165 130 235 160 T 375 158 T 445 155"
            className="tl-signal-faint-line"
          />
        </g>
        {/* Scan sweep */}
        <line x1="60" y1="20" x2="60" y2="195" className="tl-signal-sweep" aria-hidden="true" />
        {/* Emerging signal */}
        <g className="tl-signal-emerging" aria-hidden="true">
          <circle cx={SIGNAL.cx} cy={SIGNAL.cy} r="16" className="tl-signal-halo" />
          <circle cx={SIGNAL.cx} cy={SIGNAL.cy} r="7" className="tl-signal-node" />
          <path
            d={`M ${SIGNAL.cx} ${SIGNAL.cy + 16} L ${SIGNAL.cx} 178`}
            className="tl-signal-thread"
          />
        </g>
        {/* Labels */}
        <text x={SIGNAL.cx} y={30} textAnchor="middle" className="tl-signal-caption">
          Newly relevant signal
        </text>
        <text x={SIGNAL.cx} y={196} textAnchor="middle" className="tl-signal-caption">
          Payment Gateway · Weekly sync, 14 Jan
        </text>
      </svg>
    </div>
  );
}
