/* Entity detail sections: current state, unified history, risks, explicit
 * dependencies and impact, related meetings, changes, memory, and actions.
 * Every section owns one real backend question and degrades independently.
 * Entity-name joins are bounded and tenant-scoped; no text is invented.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, StatusBadge } from "../../components/ui/Badge";
import { Card, Section } from "../../components/ui/Card";
import { EmptyState, ErrorState, Skeleton } from "../../components/feedback/States";
import { attentionLevelTone } from "../../components/ui/status";
import { changeTypeLabel, shortId } from "../dashboard/dashboardFormat";
import type {
  DependencyPath,
  EntityResponse,
  StateObservation,
  TimelineEvent,
} from "../../types/entities";
import {
  ENTITY_CHANGES_LIMIT,
  ENTITY_IMPACT_GRAPH_DEPTH,
  ENTITY_RELATED_MEETING_LIMIT,
  useEntityActions,
  useEntityAttention,
  useEntityChanges,
  useEntityDependencyGraph,
  useEntityImpacts,
  useEntityInsights,
  useEntityMemory,
  useEntityRelationships,
  useEntityTemporal,
  useEntityUnifiedTimeline,
  useWorkspaceEntityNames,
} from "./useEntities";
import {
  actionPriorityTone,
  actionTypeLabel,
  attentionLevelLabel,
  attentionReasonLabel,
  describeAttention,
  describeEntityRelationship,
  evidenceTypeLabel,
  formatEntityDateTime,
  formatEntityDay,
  impactLevelTone,
  insightSeverityTone,
  memoryFactLabel,
  relationshipPhrase,
  selectEntityMeetingsFromTemporal,
  selectLatestInsight,
  selectLatestTimelineEvent,
  selectRiskSignals,
  temporalStateLabel,
  temporalStateTone,
} from "./entitiesFormat";

const TIMELINE_DISPLAY_LIMIT = 12;
const ASSOCIATION_DISPLAY_LIMIT = 12;
const RELATION_PATH_DISPLAY_LIMIT = 10;
const IMPACT_DISPLAY_LIMIT = 10;

function entityHref(entityId: string): string {
  return `/app/entities/${encodeURIComponent(entityId)}`;
}

function meetingHref(meetingId: string): string {
  return `/app/meetings/${encodeURIComponent(meetingId)}`;
}

function entityDisplayName(directory: Map<string, string>, entityId: string | null): string {
  if (!entityId) return "Unknown entity";
  return directory.get(entityId) ?? `Entity ${shortId(entityId)}`;
}

function SectionSkeleton({ lines = 4, label }: { lines?: number; label: string }): React.JSX.Element {
  return (
    <div className="tl-entity-loading-block">
      <Skeleton lines={lines} label={label} />
    </div>
  );
}

function useMeetingDirectory(entityId: string): Map<string, { title: string; date: string }> {
  const temporal = useEntityTemporal(entityId);
  return useMemo(() => {
    const directory = new Map<string, { title: string; date: string }>();
    for (const observation of temporal.data?.timeline ?? []) {
      if (!directory.has(observation.meeting_id)) {
        directory.set(observation.meeting_id, {
          title: observation.meeting_title,
          date: observation.meeting_date,
        });
      }
    }
    return directory;
  }, [temporal.data]);
}

function MeetingLink({
  meetingId,
  directory,
}: {
  meetingId: string | null;
  directory: Map<string, { title: string; date: string }>;
}): React.JSX.Element | null {
  if (!meetingId) return null;
  const meeting = directory.get(meetingId);
  return (
    <Link className="tl-entity-link" to={meetingHref(meetingId)}>
      {meeting ? `Open ${meeting.title}` : "Open meeting"}
    </Link>
  );
}

function selectLatestStateTransition(
  timeline: StateObservation[] | undefined,
): StateObservation | null {
  const transitions = (timeline ?? []).filter(
    (observation) => observation.transition_occurred && observation.from_state !== observation.to_state,
  );
  return transitions.length > 0 ? transitions[transitions.length - 1] : null;
}

export function EntityCurrentStateSection({ entityId }: { entityId: string }): React.JSX.Element {
  const temporal = useEntityTemporal(entityId);
  const attention = useEntityAttention(entityId);
  const latestTransition = useMemo(
    () => selectLatestStateTransition(temporal.data?.timeline),
    [temporal.data],
  );

  if (temporal.isPending && attention.isPending) {
    return (
      <Section
        title="Current state"
        description="What ThreadLine currently understands from this entity’s resolved observations."
      >
        <SectionSkeleton label="Loading current entity state" />
      </Section>
    );
  }

  return (
    <Section
      title="Current state"
      description="What ThreadLine currently understands from this entity’s resolved observations."
    >
      {temporal.isError ? (
        <ErrorState
          title="Couldn't load current state"
          error={temporal.error}
          onRetry={() => void temporal.refetch()}
        />
      ) : temporal.data ? (
        <Card>
          <dl className="tl-facts">
            <div>
              <dt>Current state</dt>
              <dd>
                <StatusBadge
                  tone={temporalStateTone(temporal.data.current_state)}
                  label={temporalStateLabel(temporal.data.current_state)}
                />
              </dd>
            </div>
            <div>
              <dt>Observations</dt>
              <dd className="tl-numeric">{temporal.data.observation_count}</dd>
            </div>
            <div>
              <dt>State transitions</dt>
              <dd className="tl-numeric">{temporal.data.transition_count}</dd>
            </div>
            <div>
              <dt>Latest transition</dt>
              <dd>
                {latestTransition ? (
                  <span>
                    {`${temporalStateLabel(latestTransition.from_state)} → ${temporalStateLabel(
                      latestTransition.to_state,
                    )}`}
                    <span className="tl-entity-row-meta">
                      {formatEntityDateTime(latestTransition.meeting_date) ?? "Date not provided"}
                      {` · ${latestTransition.meeting_title}`}
                    </span>
                  </span>
                ) : (
                  <span className="tl-muted">No valid state transition recorded.</span>
                )}
              </dd>
            </div>
          </dl>
        </Card>
      ) : null}
      {attention.isPending ? <SectionSkeleton lines={3} label="Loading current attention" /> : null}
      {!attention.isPending && attention.isError ? (
        <ErrorState
          title="Couldn't load current attention"
          error={attention.error}
          onRetry={() => void attention.refetch()}
        />
      ) : null}
      {!attention.isPending && !attention.isError && attention.data ? (
        <Card>
          {attention.data.has_attention && attention.data.attention ? (
            <dl className="tl-facts">
              <div>
                <dt>Attention</dt>
                <dd>
                  <StatusBadge
                    tone={attentionLevelTone(attention.data.attention.attention_level)}
                    label={attentionLevelLabel(attention.data.attention.attention_level)}
                  />
                </dd>
              </div>
              <div>
                <dt>Score</dt>
                <dd className="tl-numeric">{attention.data.attention.score}</dd>
              </div>
              <div>
                <dt>Reasons</dt>
                <dd>{attention.data.attention.reasons.map(attentionReasonLabel).join(", ")}</dd>
              </div>
              <div>
                <dt>Evaluated</dt>
                <dd>
                  {formatEntityDateTime(attention.data.attention.evaluated_at) ?? "Date not provided"}
                </dd>
              </div>
            </dl>
          ) : (
            <EmptyState
              title="No attention signals"
              body="ThreadLine currently sees no actionable reason to prioritise this entity."
            />
          )}
        </Card>
      ) : null}
    </Section>
  );
}

function timelineEventKindLabel(eventType: TimelineEvent["event_type"]): string {
  switch (eventType) {
    case "OBSERVATION":
      return "Observation";
    case "STATE_CHANGE":
      return "State change";
    case "MEMORY_FACT":
      return "Memory fact";
    case "INSIGHT":
      return "Insight";
    case "ATTENTION":
      return "Attention";
    case "ACTION":
      return "Action";
  }
}

export function EntityTimelineSection({ entityId }: { entityId: string }): React.JSX.Element {
  const timeline = useEntityUnifiedTimeline(entityId);
  const meetingDirectory = useMeetingDirectory(entityId);
  const [showAll, setShowAll] = useState(false);
  const events = useMemo(() => [...(timeline.data?.events ?? [])].reverse(), [timeline.data]);
  const visible = showAll ? events : events.slice(0, TIMELINE_DISPLAY_LIMIT);

  return (
    <Section
      title="Timeline"
      description="The organisation’s chronological record for this entity, newest first."
    >
      {timeline.isPending ? <SectionSkeleton lines={6} label="Loading entity timeline" /> : null}
      {!timeline.isPending && timeline.isError ? (
        <ErrorState
          title="Couldn't load the timeline"
          error={timeline.error}
          onRetry={() => void timeline.refetch()}
        />
      ) : null}
      {!timeline.isPending && !timeline.isError && timeline.data ? (
        events.length === 0 ? (
          <EmptyState
            title="No history yet"
            body="This entity has no timeline events. Observations will appear here once resolved mentions exist."
          />
        ) : (
          <>
            <p className="tl-entity-result-count" role="status">
              {showAll
                ? `Showing all ${events.length} timeline ${events.length === 1 ? "event" : "events"}.`
                : `Showing the latest ${visible.length} of ${events.length} timeline events.`}
            </p>
            <ol className="tl-entity-timeline">
              {visible.map((event) => (
                <li key={event.event_id}>
                  <div className="tl-entity-timeline-meta">
                    <Badge tone="neutral">{timelineEventKindLabel(event.event_type)}</Badge>
                    <span>{formatEntityDateTime(event.occurred_at) ?? "Date not provided"}</span>
                    <MeetingLink meetingId={event.related_meeting_id} directory={meetingDirectory} />
                  </div>
                  <p className="tl-entity-timeline-title">{event.title}</p>
                  <p className="tl-entity-timeline-description">{event.description}</p>
                </li>
              ))}
            </ol>
            {events.length > TIMELINE_DISPLAY_LIMIT ? (
              <button
                type="button"
                className="tl-btn tl-btn-secondary tl-btn-sm"
                aria-expanded={showAll}
                onClick={() => setShowAll((open) => !open)}
              >
                {showAll ? "Show latest events only" : `Show all ${events.length} events`}
              </button>
            ) : null}
          </>
        )
      ) : null}
    </Section>
  );
}

export function EntityRiskSection({ entityId }: { entityId: string }): React.JSX.Element {
  const insights = useEntityInsights(entityId);
  const meetingDirectory = useMeetingDirectory(entityId);
  const risks = useMemo(() => selectRiskSignals(insights.data?.insights), [insights.data]);

  return (
    <Section
      title="Risk and unresolved signals"
      description="Warning and critical insights only. Informational state changes are history, not risks."
    >
      {insights.isPending ? <SectionSkeleton lines={4} label="Loading risk signals" /> : null}
      {!insights.isPending && insights.isError ? (
        <ErrorState
          title="Couldn't load risk signals"
          error={insights.error}
          onRetry={() => void insights.refetch()}
        />
      ) : null}
      {!insights.isPending && !insights.isError && insights.data ? (
        risks.length === 0 ? (
          <EmptyState
            title="No active risk signals"
            body="ThreadLine has not derived a warning or critical insight for this entity."
          />
        ) : (
          <ul className="tl-entity-rows">
            {risks.map((insight) => (
              <li key={insight.insight_id}>
                <div className="tl-entity-row-head">
                  <p className="tl-entity-timeline-title">{insight.title}</p>
                  <StatusBadge tone={insightSeverityTone(insight.severity)} label={insight.severity} />
                </div>
                <p className="tl-entity-row-detail">{insight.description}</p>
                <blockquote className="tl-entity-quote">{insight.evidence}</blockquote>
                <div className="tl-entity-row-actions">
                  <span>{formatEntityDateTime(insight.observed_at) ?? "Date not provided"}</span>
                  <MeetingLink meetingId={insight.related_meeting_id} directory={meetingDirectory} />
                </div>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </Section>
  );
}

function ExplicitDependencyList({ entityId }: { entityId: string }): React.JSX.Element {
  const relationships = useEntityRelationships(entityId);
  const outgoing = useMemo(
    () =>
      (relationships.data?.relationships ?? []).filter(
        (relationship) =>
          relationship.relationship_type === "DEPENDS_ON" &&
          relationship.source_entity_id === entityId,
      ),
    [relationships.data, entityId],
  );
  const incoming = useMemo(
    () =>
      (relationships.data?.relationships ?? []).filter(
        (relationship) =>
          relationship.relationship_type === "DEPENDS_ON" &&
          relationship.target_entity_id === entityId,
      ),
    [relationships.data, entityId],
  );
  const blocking = useMemo(
    () =>
      (relationships.data?.relationships ?? []).filter(
        (relationship) => relationship.relationship_type === "BLOCKS",
      ),
    [relationships.data],
  );
  const names = useWorkspaceEntityNames(
    useMemo(
      () =>
        (relationships.data?.relationships ?? []).flatMap((relationship) => [
          relationship.source_entity_id,
          relationship.target_entity_id,
        ]),
      [relationships.data],
    ),
  );

  if (relationships.isPending || (relationships.data && names.isLoading)) {
    return <SectionSkeleton lines={4} label="Loading explicit dependencies" />;
  }
  if (relationships.isError) {
    return (
      <ErrorState
        title="Couldn't load explicit dependencies"
        error={relationships.error}
        onRetry={() => void relationships.refetch()}
      />
    );
  }
  if (!relationships.data) return <></>;
  if (outgoing.length === 0 && incoming.length === 0 && blocking.length === 0) {
    return (
      <EmptyState
        title="No explicit dependencies recorded"
        body="ThreadLine only lists a dependency when meeting evidence explicitly states it. Co-occurrence is shown separately below."
      />
    );
  }

  return (
    <>
      {outgoing.length > 0 ? (
        <div>
          <h3 className="tl-card-title">This entity depends on</h3>
          <ul className="tl-entity-rows">
            {outgoing.map((relationship) => (
              <li key={relationship.relationship_id}>
                <Link className="tl-entity-row-title-link" to={entityHref(relationship.target_entity_id)}>
                  {entityDisplayName(names.directory, relationship.target_entity_id)}
                </Link>
                <p className="tl-entity-row-detail">
                  {`${relationshipPhrase(relationship.relationship_type)} · ${evidenceTypeLabel(
                    relationship.evidence_type,
                  )} · observed in ${relationship.related_meeting_ids.length} ${
                    relationship.related_meeting_ids.length === 1 ? "meeting" : "meetings"
                  }.`}
                </p>
                {relationship.source_text ? (
                  <blockquote className="tl-entity-quote">{relationship.source_text}</blockquote>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {incoming.length > 0 ? (
        <div>
          <h3 className="tl-card-title">Required by</h3>
          <ul className="tl-entity-rows">
            {incoming.map((relationship) => (
              <li key={relationship.relationship_id}>
                <Link className="tl-entity-row-title-link" to={entityHref(relationship.source_entity_id)}>
                  {entityDisplayName(names.directory, relationship.source_entity_id)}
                </Link>
                <p className="tl-entity-row-detail">
                  {`Explicit statement · observed in ${relationship.related_meeting_ids.length} ${
                    relationship.related_meeting_ids.length === 1 ? "meeting" : "meetings"
                  }.`}
                </p>
                {relationship.source_text ? (
                  <blockquote className="tl-entity-quote">{relationship.source_text}</blockquote>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {blocking.length > 0 ? (
        <div>
          <h3 className="tl-card-title">Blocking evidence</h3>
          <ul className="tl-entity-rows">
            {blocking.map((relationship) => {
              const connection = describeEntityRelationship(relationship, entityId);
              return (
                <li key={relationship.relationship_id}>
                  <p className="tl-entity-timeline-title">
                    {connection.otherEntityId ? (
                      <Link
                        className="tl-entity-row-title-link"
                        to={entityHref(connection.otherEntityId)}
                      >
                        {entityDisplayName(names.directory, connection.otherEntityId)}
                      </Link>
                    ) : (
                      "Unknown entity"
                    )}{" "}
                    · {connection.phrase}
                  </p>
                  <p className="tl-entity-row-detail">{relationship.evidence}</p>
                  {relationship.source_text ? (
                    <blockquote className="tl-entity-quote">{relationship.source_text}</blockquote>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
      {names.isError ? (
        <p className="tl-entity-section-note" role="status">
          Some linked entity names are temporarily unavailable.
        </p>
      ) : null}
    </>
  );
}

function DependencyPathList({ entityId }: { entityId: string }): React.JSX.Element {
  const graph = useEntityDependencyGraph(entityId);
  const names = useWorkspaceEntityNames(
    useMemo(
      () => [
        ...(graph.data?.direct_dependencies.flatMap((path) => path.entity_path) ?? []),
        ...(graph.data?.transitive_dependencies.flatMap((path) => path.entity_path) ?? []),
      ],
      [graph.data],
    ),
  );

  if (graph.isPending || (graph.data && names.isLoading)) {
    return <SectionSkeleton lines={3} label="Loading dependency paths" />;
  }
  if (graph.isError) {
    return (
      <ErrorState
        title="Couldn't load dependency paths"
        error={graph.error}
        onRetry={() => void graph.refetch()}
      />
    );
  }
  if (!graph.data) return <></>;
  const paths = [...graph.data.direct_dependencies, ...graph.data.transitive_dependencies];
  if (paths.length === 0) {
    return (
      <EmptyState
        title="No dependency paths"
        body="No outgoing explicit dependency path was found within the requested traversal depth."
      />
    );
  }
  const visible = paths.slice(0, RELATION_PATH_DISPLAY_LIMIT);

  return (
    <>
      <p className="tl-entity-result-count" role="status">
        {`Showing ${visible.length} of ${paths.length} explicit dependency ${
          paths.length === 1 ? "path" : "paths"
        } · maximum depth ${graph.data.max_depth_reached}.`}
        {graph.data.contains_cycle ? " A dependency cycle is present." : ""}
      </p>
      <ol className="tl-entity-rows">
        {visible.map((path: DependencyPath) => (
          <li key={path.path_id}>
            <p className="tl-entity-timeline-title">
              {path.is_direct ? "Direct path" : `Transitive path · depth ${path.depth}`}
            </p>
            <p className="tl-entity-path">
              {path.entity_path.map((pathEntityId, index) => (
                <span key={`${path.path_id}-${pathEntityId}-${index}`}>
                  <Link className="tl-entity-link" to={entityHref(pathEntityId)}>
                    {entityDisplayName(names.directory, pathEntityId)}
                  </Link>
                  {index < path.entity_path.length - 1 ? (
                    <span className="tl-entity-path-separator" aria-hidden="true">
                      {" → "}
                    </span>
                  ) : null}
                </span>
              ))}
            </p>
            <p className="tl-entity-row-detail">
              {path.relationship_path.map(relationshipPhrase).join(" → ")}
            </p>
          </li>
        ))}
      </ol>
      {names.isError ? (
        <p className="tl-entity-section-note" role="status">
          Some path names are temporarily unavailable.
        </p>
      ) : null}
    </>
  );
}

function ObservedAssociations({ entityId }: { entityId: string }): React.JSX.Element {
  const relationships = useEntityRelationships(entityId);
  const [showAll, setShowAll] = useState(false);
  const associations = useMemo(
    () =>
      (relationships.data?.relationships ?? []).filter(
        (relationship) =>
          relationship.relationship_type === "CO_OCCURS_WITH" ||
          relationship.relationship_type === "RELATED_TO",
      ),
    [relationships.data],
  );
  const names = useWorkspaceEntityNames(
    useMemo(
      () =>
        associations.flatMap((relationship) => [
          relationship.source_entity_id,
          relationship.target_entity_id,
        ]),
      [associations],
    ),
  );

  if (relationships.isPending || (relationships.data && names.isLoading)) {
    return <SectionSkeleton lines={3} label="Loading observed associations" />;
  }
  if (relationships.isError) {
    return (
      <ErrorState
        title="Couldn't load observed associations"
        error={relationships.error}
        onRetry={() => void relationships.refetch()}
      />
    );
  }
  if (!relationships.data || associations.length === 0) {
    return (
      <EmptyState
        title="No observed associations"
        body="This entity has not yet been observed alongside another canonical entity."
      />
    );
  }
  const visible = showAll ? associations : associations.slice(0, ASSOCIATION_DISPLAY_LIMIT);

  return (
    <>
      <ul className="tl-entity-rows">
        {visible.map((relationship) => {
          const connection = describeEntityRelationship(relationship, entityId);
          return (
            <li key={relationship.relationship_id}>
              {connection.otherEntityId ? (
                <Link
                  className="tl-entity-row-title-link"
                  to={entityHref(connection.otherEntityId)}
                >
                  {entityDisplayName(names.directory, connection.otherEntityId)}
                </Link>
              ) : (
                <span className="tl-muted">Unknown entity</span>
              )}
              <p className="tl-entity-row-detail">
                {`${connection.phrase} · ${relationship.evidence} · ${evidenceTypeLabel(
                  relationship.evidence_type,
                )}.`}
              </p>
            </li>
          );
        })}
      </ul>
      {associations.length > ASSOCIATION_DISPLAY_LIMIT ? (
        <button
          type="button"
          className="tl-btn tl-btn-secondary tl-btn-sm"
          aria-expanded={showAll}
          onClick={() => setShowAll((open) => !open)}
        >
          {showAll
            ? "Show fewer associations"
            : `Show all ${associations.length} associations`}
        </button>
      ) : null}
    </>
  );
}

export function EntityDependencySection({ entityId }: { entityId: string }): React.JSX.Element {
  return (
    <Section
      title="Dependencies"
      description="Explicit dependency evidence only. Meeting co-occurrence never becomes a dependency."
    >
      <ExplicitDependencyList entityId={entityId} />
      <DependencyPathList entityId={entityId} />
      <ObservedAssociations entityId={entityId} />
    </Section>
  );
}

export function EntityImpactSection({ entityId }: { entityId: string }): React.JSX.Element {
  const impacts = useEntityImpacts(entityId);
  const names = useWorkspaceEntityNames(
    useMemo(
      () => (impacts.data?.impacts ?? []).map((impact) => impact.source_entity_id),
      [impacts.data],
    ),
  );

  return (
    <Section
      title="Potential impact"
      description="Risk associations directed at this entity. Association is not causation."
    >
      {impacts.isPending || (impacts.data && names.isLoading) ? (
        <SectionSkeleton lines={4} label="Loading potential impact" />
      ) : null}
      {!impacts.isPending && impacts.isError ? (
        <ErrorState
          title="Couldn't load potential impact"
          error={impacts.error}
          onRetry={() => void impacts.refetch()}
        />
      ) : null}
      {!impacts.isPending && !impacts.isError && impacts.data ? (
        impacts.data.impacts.length === 0 ? (
          <EmptyState
            title="No associated impact"
            body="No risk impact association is currently directed at this entity."
          />
        ) : (
          <>
            <p className="tl-entity-result-count" role="status">
              {`${impacts.data.impact_count} associated ${
                impacts.data.impact_count === 1 ? "impact" : "impacts"
              }, evaluated at depth ${ENTITY_IMPACT_GRAPH_DEPTH}.`}
            </p>
            <ul className="tl-entity-rows">
              {impacts.data.impacts.slice(0, IMPACT_DISPLAY_LIMIT).map((impact) => (
                <li key={impact.impact_id}>
                  <div className="tl-entity-row-head">
                    <Link
                      className="tl-entity-row-title-link"
                      to={entityHref(impact.source_entity_id)}
                    >
                      {entityDisplayName(names.directory, impact.source_entity_id)}
                    </Link>
                    <StatusBadge tone={impactLevelTone(impact.impact_level)} label={impact.impact_level} />
                  </div>
                  <p className="tl-entity-row-detail">{impact.reason}</p>
                  <div className="tl-entity-row-actions">
                    <span>
                      {`Strength ${impact.relationship_strength} · signals ${impact.risk_signals.join(", ")}`}
                    </span>
                    <span>
                      {formatEntityDateTime(impact.generated_from_at) ?? "Date not provided"}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </>
        )
      ) : null}
    </Section>
  );
}

export function EntityMeetingsSection({ entityId }: { entityId: string }): React.JSX.Element {
  const temporal = useEntityTemporal(entityId);
  const meetings = useMemo(
    () => selectEntityMeetingsFromTemporal(temporal.data?.timeline, ENTITY_RELATED_MEETING_LIMIT),
    [temporal.data],
  );
  const totalMeetings = useMemo(
    () => new Set((temporal.data?.timeline ?? []).map((observation) => observation.meeting_id)).size,
    [temporal.data],
  );

  return (
    <Section
      title="Related meetings"
      description="Meetings where this entity was observed and resolved, newest first."
    >
      {temporal.isPending ? <SectionSkeleton lines={4} label="Loading related meetings" /> : null}
      {!temporal.isPending && temporal.isError ? (
        <ErrorState
          title="Couldn't load related meetings"
          error={temporal.error}
          onRetry={() => void temporal.refetch()}
        />
      ) : null}
      {!temporal.isPending && !temporal.isError && temporal.data ? (
        meetings.length === 0 ? (
          <EmptyState
            title="No meetings currently associated"
            body="No resolved observation of this entity has been recorded in a meeting yet."
          />
        ) : (
          <>
            <p className="tl-entity-result-count" role="status">
              {`Showing ${meetings.length} of ${totalMeetings} associated ${
                totalMeetings === 1 ? "meeting" : "meetings"
              }.`}
            </p>
            <ul className="tl-entity-rows">
              {meetings.map((meeting) => (
                <li key={meeting.meeting_id}>
                  <Link className="tl-entity-row-title-link" to={meetingHref(meeting.meeting_id)}>
                    {meeting.title}
                  </Link>
                  <p className="tl-entity-row-detail">
                    {`${formatEntityDateTime(meeting.meeting_date) ?? "Date not provided"} · ${
                      meeting.observationCount
                    } ${meeting.observationCount === 1 ? "observation" : "observations"}`}
                  </p>
                </li>
              ))}
            </ul>
          </>
        )
      ) : null}
    </Section>
  );
}

export function EntityChangesSection({ entityId }: { entityId: string }): React.JSX.Element {
  const changes = useEntityChanges(entityId);
  const meetingDirectory = useMeetingDirectory(entityId);

  return (
    <Section
      title="Changes"
      description="Deterministic organisation changes connected to this entity, in backend order."
    >
      {changes.isPending ? <SectionSkeleton lines={4} label="Loading entity changes" /> : null}
      {!changes.isPending && changes.isError ? (
        <ErrorState
          title="Couldn't load entity changes"
          error={changes.error}
          onRetry={() => void changes.refetch()}
        />
      ) : null}
      {!changes.isPending && !changes.isError && changes.data ? (
        changes.data.changes.length === 0 ? (
          <EmptyState
            title="No changes attributed to this entity"
            body="State transitions, escalations, dependencies, and impacts connected to this entity will appear here."
          />
        ) : (
          <>
            <p className="tl-entity-result-count" role="status">
              {`Showing ${changes.data.changes.length} of ${changes.data.total_changes} attributed ${
                changes.data.total_changes === 1 ? "change" : "changes"
              } · limit ${ENTITY_CHANGES_LIMIT}.`}
            </p>
            <ul className="tl-entity-rows">
              {changes.data.changes.map((change) => (
                <li key={change.change_id}>
                  <div className="tl-entity-row-head">
                    <p className="tl-entity-timeline-title">{changeTypeLabel(change.change_type)}</p>
                    <StatusBadge
                      tone={
                        change.severity === "CRITICAL"
                          ? "danger"
                          : change.severity === "HIGH"
                            ? "warning"
                            : change.severity === "MEDIUM"
                              ? "info"
                              : "neutral"
                      }
                      label={change.severity}
                    />
                  </div>
                  <p className="tl-entity-row-detail">{change.evidence}</p>
                  <div className="tl-entity-row-actions">
                    <span>{formatEntityDateTime(change.detected_at) ?? "Date not provided"}</span>
                    <MeetingLink meetingId={change.meeting_id} directory={meetingDirectory} />
                  </div>
                </li>
              ))}
            </ul>
          </>
        )
      ) : null}
    </Section>
  );
}

function evidenceByMentionId(
  timeline: StateObservation[] | undefined,
): Map<string, string> {
  const evidence = new Map<string, string>();
  for (const observation of timeline ?? []) {
    if (!evidence.has(observation.mention_id)) {
      evidence.set(observation.mention_id, observation.evidence_text);
    }
  }
  return evidence;
}

export function EntityMemorySection({ entityId }: { entityId: string }): React.JSX.Element {
  const memory = useEntityMemory(entityId);
  const temporal = useEntityTemporal(entityId);
  const meetingDirectory = useMeetingDirectory(entityId);
  const evidence = useMemo(() => evidenceByMentionId(temporal.data?.timeline), [temporal.data]);

  return (
    <Section
      title="Memory and evidence"
      description="What the organisation currently knows, with each fact traced to its source where one exists."
    >
      {memory.isPending ? <SectionSkeleton lines={5} label="Loading entity memory" /> : null}
      {!memory.isPending && memory.isError ? (
        <ErrorState
          title="Couldn't load entity memory"
          error={memory.error}
          onRetry={() => void memory.refetch()}
        />
      ) : null}
      {!memory.isPending && !memory.isError && memory.data ? (
        <ul className="tl-entity-rows">
          {memory.data.facts.map((fact, index) => {
            const sourceEvidence =
              fact.source_mention_id !== null ? evidence.get(fact.source_mention_id) : undefined;
            return (
              <li key={`${fact.fact_type}-${fact.value}-${index}`}>
                <div className="tl-entity-row-head">
                  <p className="tl-entity-timeline-title">{memoryFactLabel(fact.fact_type)}</p>
                  {fact.fact_type === "CURRENT_STATE" ? (
                    <StatusBadge
                      tone={temporalStateTone(fact.value)}
                      label={temporalStateLabel(fact.value)}
                    />
                  ) : null}
                </div>
                <p className="tl-entity-row-detail">{fact.value}</p>
                {fact.detail ? <p className="tl-entity-row-detail">{fact.detail}</p> : null}
                {sourceEvidence ? (
                  <blockquote className="tl-entity-quote">{sourceEvidence}</blockquote>
                ) : null}
                <div className="tl-entity-row-actions">
                  <span>
                    {fact.observed_at
                      ? (formatEntityDateTime(fact.observed_at) ?? "Date not provided")
                      : fact.fact_type === "CURRENT_STATE"
                        ? "Organisation-wide aggregate — not tied to one meeting"
                        : "Date not provided"}
                  </span>
                  <MeetingLink meetingId={fact.source_meeting_id} directory={meetingDirectory} />
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
    </Section>
  );
}

export function EntityAttentionActionsSection({ entityId }: { entityId: string }): React.JSX.Element {
  const attention = useEntityAttention(entityId);
  const actions = useEntityActions(entityId);
  const meetingDirectory = useMeetingDirectory(entityId);

  if (attention.isPending && actions.isPending) {
    return (
      <Section
        title="Attention and recommended actions"
        description="Current prioritisation and deterministic next actions from backend intelligence."
      >
        <SectionSkeleton label="Loading attention and actions" />
      </Section>
    );
  }

  return (
    <Section
      title="Attention and recommended actions"
      description="Current prioritisation and deterministic next actions from backend intelligence."
    >
      {!attention.isPending && attention.isError ? (
        <ErrorState
          title="Couldn't load attention"
          error={attention.error}
          onRetry={() => void attention.refetch()}
        />
      ) : null}
      {!attention.isPending && !attention.isError && attention.data ? (
        attention.data.has_attention && attention.data.attention ? (
          <p className="tl-entity-row-detail">{describeAttention(attention.data.attention)}</p>
        ) : (
          <EmptyState
            title="No attention signals"
            body="ThreadLine currently sees no actionable reason to prioritise this entity."
          />
        )
      ) : null}
      {actions.isPending ? <SectionSkeleton lines={3} label="Loading recommended actions" /> : null}
      {!actions.isPending && actions.isError ? (
        <ErrorState
          title="Couldn't load recommended actions"
          error={actions.error}
          onRetry={() => void actions.refetch()}
        />
      ) : null}
      {!actions.isPending && !actions.isError && actions.data ? (
        actions.data.actions.length === 0 ? (
          <EmptyState
            title="No recommended actions"
            body="No deterministic next action is currently recommended for this entity."
          />
        ) : (
          <ul className="tl-entity-rows">
            {actions.data.actions.map((action) => (
              <li key={action.action_id}>
                <div className="tl-entity-row-head">
                  <p className="tl-entity-timeline-title">{action.recommended_action}</p>
                  <StatusBadge tone={actionPriorityTone(action.priority)} label={action.priority} />
                </div>
                <p className="tl-entity-row-detail">
                  {`${actionTypeLabel(action.action_type)} · ${action.reason}`}
                </p>
                <div className="tl-entity-row-actions">
                  <span>
                    {formatEntityDateTime(action.created_from_observation_at) ?? "Date not provided"}
                  </span>
                  <MeetingLink meetingId={action.related_meeting_id} directory={meetingDirectory} />
                </div>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </Section>
  );
}

export function EntityMetadataRail({
  entity,
  entityId,
}: {
  entity: EntityResponse;
  entityId: string;
}): React.JSX.Element {
  const memory = useEntityMemory(entityId);
  const attention = useEntityAttention(entityId);
  const insights = useEntityInsights(entityId);
  const latestInsight = useMemo(
    () => selectLatestInsight(insights.data?.insights),
    [insights.data],
  );
  const latestEvent = useEntityUnifiedTimeline(entityId);
  const latestTimelineEvent = useMemo(
    () => selectLatestTimelineEvent(latestEvent.data?.events),
    [latestEvent.data],
  );

  return (
    <aside className="tl-entity-rail" aria-label="Entity details">
      <Section title="Key context">
        <Card>
          <dl className="tl-facts">
            <div>
              <dt>Type</dt>
              <dd>{entity.entity_type}</dd>
            </div>
            <div>
              <dt>Aliases</dt>
              <dd>
                {entity.aliases && entity.aliases.length > 0 ? (
                  <span className="tl-badge-row">
                    {entity.aliases.map((alias) => (
                      <Badge key={alias}>{alias}</Badge>
                    ))}
                  </span>
                ) : (
                  <span className="tl-muted">No aliases recorded.</span>
                )}
              </dd>
            </div>
            <div>
              <dt>First observed</dt>
              <dd>
                {memory.isPending
                  ? "Loading observation history…"
                  : memory.isError
                    ? "Observation history unavailable"
                    : (formatEntityDay(memory.data?.first_observed_at) ?? "No observations yet")}
              </dd>
            </div>
            <div>
              <dt>Last observed</dt>
              <dd>
                {memory.isPending
                  ? "Loading observation history…"
                  : memory.isError
                    ? "Observation history unavailable"
                    : (formatEntityDay(memory.data?.last_observed_at) ?? "No observations yet")}
              </dd>
            </div>
            <div>
              <dt>Evidence trail</dt>
              <dd>
                {memory.isPending
                  ? "Loading observation history…"
                  : memory.isError
                    ? "Observation history unavailable"
                    : `${memory.data?.observation_count ?? 0} observations · ${
                        memory.data?.meeting_count ?? 0
                      } meetings`}
              </dd>
            </div>
            <div>
              <dt>Attention</dt>
              <dd>
                {attention.isPending
                  ? "Loading attention…"
                  : attention.isError
                    ? "Attention unavailable"
                    : (describeAttention(attention.data?.attention) ?? "No attention signals")}
              </dd>
            </div>
            <div>
              <dt>Latest insight</dt>
              <dd>
                {insights.isPending
                  ? "Loading insights…"
                  : insights.isError
                    ? "Insights unavailable"
                    : (latestInsight?.title ?? "No insights derived")}
              </dd>
            </div>
            <div>
              <dt>Latest event</dt>
              <dd>
                {latestEvent.isPending
                  ? "Loading timeline…"
                  : latestEvent.isError
                    ? "Timeline unavailable"
                    : (latestTimelineEvent?.title ?? "No history yet")}
              </dd>
            </div>
          </dl>
        </Card>
      </Section>
    </aside>
  );
}
