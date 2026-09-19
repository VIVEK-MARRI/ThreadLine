/* EvidenceChain — traceability visual.
 * Intelligence Signal -> Entity -> Meeting -> Source Excerpt.
 * Interactive: hover or keyboard-focus a step to highlight its thread
 * back toward the source. Fully keyboard accessible. */

import { useState } from "react";
import { LANDING } from "./landingContent";
import { useReveal } from "./useLandingAnimations";

export function EvidenceChain(): React.JSX.Element {
  const { ref, revealed } = useReveal(0.2);
  const [activeIdx, setActiveIdx] = useState<number | null>(null);
  const { chain } = LANDING.evidence;

  return (
    <div
      ref={ref as React.RefObject<HTMLDivElement>}
      className={`tl-evidence-chain ${revealed ? "tl-revealed" : ""}`}
      role="list"
      aria-label="Evidence traceability chain"
    >
      {chain.map((item, i) => {
        const traced = activeIdx !== null && activeIdx >= i;
        return (
          <div
            key={i}
            role="listitem"
            tabIndex={0}
            aria-label={`${item.label}: ${item.detail}`}
            className={`tl-evidence-node ${traced ? "tl-evidence-node-traced" : ""}`}
            onMouseEnter={() => setActiveIdx(i)}
            onMouseLeave={() => setActiveIdx(null)}
            onFocus={() => setActiveIdx(i)}
            onBlur={() => setActiveIdx(null)}
          >
            {i > 0 && (
              <div
                className={`tl-evidence-connector ${traced ? "tl-evidence-connector-traced" : ""}`}
                aria-hidden="true"
              />
            )}
            <div className="tl-evidence-card">
              <p className="tl-evidence-card-step">
                {i + 1} of {chain.length}
              </p>
              <p className="tl-evidence-card-label">{item.label}</p>
              <p className="tl-evidence-card-detail">{item.detail}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
