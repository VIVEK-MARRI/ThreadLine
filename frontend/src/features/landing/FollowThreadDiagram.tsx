/* FollowThreadDiagram — the visual climax of the landing page.
 * Full thread from Meeting through Action on a deep navy stage.
 * Scroll-triggered stage activation via IntersectionObserver; the active
 * stage is emphasised while previous stages stay visible. A thin progress
 * line connects everything. Fully static under prefers-reduced-motion. */

import { LANDING } from "./landingContent";
import { useStageReveal } from "./useLandingAnimations";

export function FollowThreadDiagram(): React.JSX.Element {
  const { stages } = LANDING.followThread;
  const { refs, active } = useStageReveal(stages.length, 0.4);
  const reached = active.filter(Boolean).length;

  return (
    <div className="tl-follow-thread" role="list" aria-label="Follow the thread">
      <div className="tl-follow-progress" aria-hidden="true">
        <div
          className="tl-follow-progress-fill"
          style={{ height: `${(reached / stages.length) * 100}%` }}
        />
      </div>
      {stages.map((stage, i) => (
        <div
          key={stage.id}
          ref={(el) => {
            refs.current[i] = el;
          }}
          role="listitem"
          aria-current={active[i] && (i === reached - 1 || reached === 0) ? "step" : undefined}
          className={`tl-follow-stage ${active[i] ? "tl-follow-stage-active" : ""} ${
            i === reached - 1 ? "tl-follow-stage-current" : ""
          }`}
        >
          <div className="tl-follow-dot" aria-hidden="true">
            <span />
          </div>
          <div className="tl-follow-content">
            <p className="tl-follow-index" aria-hidden="true">
              {String(i + 1).padStart(2, "0")}
            </p>
            <p className="tl-follow-label">{stage.label}</p>
            <p className="tl-follow-detail">{stage.detail}</p>
          </div>
        </div>
      ))}
    </div>
  );
}
