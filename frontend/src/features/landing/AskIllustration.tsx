/* AskIllustration — product illustration of the Ask experience.
 * Staged sequence: question -> searching -> answer -> cited evidence.
 * Fast and elegant; no fake typing animation. All content illustrative. */

import { LANDING } from "./landingContent";
import { usePrefersReducedMotion, useReveal } from "./useLandingAnimations";

export function AskIllustration(): React.JSX.Element {
  const { ref, revealed } = useReveal(0.2);
  const reduced = usePrefersReducedMotion();
  const {
    question,
    questionLabel,
    searchingLabel,
    answer,
    answerLabel,
    evidence,
    evidenceLabel,
    illustrativeNote,
  } = LANDING.ask;

  return (
    <div
      ref={ref as React.RefObject<HTMLDivElement>}
      className={`tl-ask-illustration ${revealed ? "tl-revealed" : ""} ${reduced ? "tl-ask-instant" : ""}`}
      role="group"
      aria-label="Illustrated Ask ThreadLine answer with cited evidence"
    >
      {/* Question */}
      <div className="tl-ask-step tl-ask-step-1">
        <p className="tl-ask-step-label">{questionLabel}</p>
        <div className="tl-ask-bubble tl-ask-question">
          <p>{question}</p>
        </div>
      </div>

      {/* Retrieval indicator */}
      <div className="tl-ask-step tl-ask-step-2" aria-hidden={reduced}>
        <p className="tl-ask-searching">
          <span className="tl-ask-searching-dots" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
          {searchingLabel}
        </p>
      </div>

      {/* Answer */}
      <div className="tl-ask-step tl-ask-step-3">
        <p className="tl-ask-step-label">{answerLabel}</p>
        <div className="tl-ask-bubble tl-ask-answer tl-glass">
          <p>{answer}</p>
        </div>
      </div>

      {/* Cited evidence */}
      <div className="tl-ask-step tl-ask-step-4">
        <p className="tl-ask-step-label">{evidenceLabel}</p>
        <div className="tl-ask-evidence-list">
          {evidence.map((item, i) => (
            <div
              key={i}
              className="tl-ask-evidence-item"
              style={{ animationDelay: `${0.1 + i * 0.12}s` }}
            >
              <span className="tl-ask-evidence-type">{item.type}</span>
              <span className="tl-ask-evidence-detail">{item.detail}</span>
            </div>
          ))}
        </div>
      </div>

      <p className="tl-ask-note">{illustrativeNote}</p>
    </div>
  );
}
