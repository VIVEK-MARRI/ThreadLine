/* MemoryTransformation — conceptual Meeting-to-Memory visual.
 * Conversation fragments on the left, structured glass memory on the right.
 * Clearly labeled as illustrative. Staged reveal via CSS. */

import { LANDING } from "./landingContent";
import { useReveal } from "./useLandingAnimations";

export function MemoryTransformation(): React.JSX.Element {
  const { ref, revealed } = useReveal(0.2);
  const { conversation, extraction, illustrativeNote, conversationLabel, extractionLabel } =
    LANDING.meetingToMemory;

  return (
    <div
      ref={ref as React.RefObject<HTMLDivElement>}
      className={`tl-memory-transform ${revealed ? "tl-revealed" : ""}`}
    >
      {/* Conversation side */}
      <div className="tl-memory-conversation">
        <p className="tl-memory-label">{conversationLabel}</p>
        <div className="tl-memory-lines">
          {conversation.map((line, i) => (
            <p
              key={i}
              className="tl-memory-line"
              style={{ animationDelay: `${0.1 + i * 0.15}s` }}
            >
              <span className="tl-memory-speaker">{line.speaker}</span>
              {line.text}
            </p>
          ))}
        </div>
      </div>

      {/* Transition indicator */}
      <div className="tl-memory-arrow" aria-hidden="true">
        <svg viewBox="0 0 40 40" width="40" height="40" aria-hidden="true">
          <path
            d="M8 20h24M24 12l8 8-8 8"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>

      {/* Structured extraction side: the single glass surface in this composition */}
      <div className="tl-memory-extraction tl-glass">
        <p className="tl-memory-label">{extractionLabel}</p>
        <dl className="tl-memory-fields">
          {extraction.map((item, i) => (
            <div
              key={i}
              className="tl-memory-field"
              style={{ animationDelay: `${0.5 + i * 0.12}s` }}
            >
              <dt>{item.field}</dt>
              <dd>{item.value}</dd>
            </div>
          ))}
        </dl>
        <p className="tl-memory-note">{illustrativeNote}</p>
      </div>
    </div>
  );
}
