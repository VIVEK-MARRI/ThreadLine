/* EntityMap — premium conceptual entity workspace.
 * Center entity identity with eight facet cards (state, history, meetings,
 * dependencies, impact, attention, evidence, actions) joined by a rail.
 * Communicates the idea; it does not reproduce the application screen.
 * Clearly labeled as illustrative. */

import { LANDING } from "./landingContent";
import { useReveal } from "./useLandingAnimations";

export function EntityMap(): React.JSX.Element {
  const { ref, revealed } = useReveal(0.15);
  const { entity } = LANDING.entityIntelligence;

  return (
    <div
      ref={ref as React.RefObject<HTMLDivElement>}
      className={`tl-entity-map ${revealed ? "tl-revealed" : ""}`}
      role="group"
      aria-label={`Conceptual entity workspace for ${entity.name}`}
    >
      <div className="tl-entity-map-center tl-glass">
        <p className="tl-entity-map-kicker">Entity</p>
        <p className="tl-entity-map-name">{entity.name}</p>
        <p className="tl-entity-map-state">
          <span className="tl-entity-map-state-dot" aria-hidden="true" />
          {entity.state}
        </p>
      </div>
      <ul className="tl-entity-map-facets">
        {entity.facets.map((facet, i) => (
          <li
            key={facet.label}
            className="tl-entity-map-facet"
            style={{ animationDelay: `${0.15 + i * 0.08}s` }}
          >
            <span className="tl-entity-map-facet-dot" aria-hidden="true" />
            <div>
              <p className="tl-entity-map-facet-label">{facet.label}</p>
              <p className="tl-entity-map-facet-detail">{facet.detail}</p>
            </div>
          </li>
        ))}
      </ul>
      <p className="tl-entity-map-note">{entity.illustrativeNote}</p>
    </div>
  );
}
