/* Meeting detail sections: processing, facts, entities, dependencies,
 * changes, source transcript, and metadata. Each section owns one real
 * backend question and degrades independently.
 */

import { Link } from "react-router-dom";
import { useMemo } from "react";
import { Badge, StatusBadge } from "../../components/ui/Badge";
import { Card, Section } from "../../components/ui/Card";
import { EmptyState, ErrorState, Skeleton } from "../../components/feedback/States";
import { shortId } from "../dashboard/dashboardFormat";
import { changeTypeLabel } from "../dashboard/dashboardFormat";
import type { EntityRelationship } from "../../types/entities";
import type {
  ExtractionResponse,
  MeetingMention,
  MeetingProcessingResponse,
  MeetingResponse,
} from "../../types/meetings";
import {
  MEETING_DEPENDENCY_NAME_LIMIT,
  meetingChangeEntities,
  useMeetingChanges,
  useMeetingDependencies,
  useMeetingEntityNames,
  useMeetingExtraction,
  useMeetingMentionEntities,
  useMeetingMentions,
  useMeetingProcessing,
} from "./useMeetings";
import {
  extractionRiskTone,
  formatMeetingDateTime,
  formatMeetingDay,
  orderedMeetingMentions,
  participantPreview,
  processingStatusLabel,
  processingStatusTone,
  riskSeverityLabel,
  transcriptSizeLabel,
  uniqueResolvedEntityIds,
} from "./meetingsFormat";

export const MEETING_MENTION_DISPLAY_LIMIT = 30;

function entityHref(entityId: string): string {
  return `/app/entities/${encodeURIComponent(entityId)}`;
}

function entityDisplayName(directory: Map<string, string>, entityId: string): string {
  return directory.get(entityId) ?? `Entity ${shortId(entityId)}`;
}

function relationshipPhrase(type: EntityRelationship["relationship_type"]): string {
  switch (type) {
    case "DEPENDS_ON":
      return "depends on";
    case "BLOCKS":
      return "blocks";
    default:
      return type.replace(/_/g, " ").toLowerCase();
  }
}

function SectionSkeleton({ lines = 4, label }: { lines?: number; label: string }): React.JSX.Element {
  return (
    <div className="tl-meeting-loading-block">
      <Skeleton lines={lines} label={label} />
    </div>
  );
}

export function MeetingProcessingSection({ meetingId }: { meetingId: string }): React.JSX.Element {
  const processing = useMeetingProcessing(meetingId);
  return (
    <Section title="Processing" description="Durable source and pipeline state for the current revision.">
      {processing.isLoading ? <SectionSkeleton label="Loading processing state" /> : null}
      {!processing.isLoading && processing.isError ? (
        <ErrorState
          title="Couldn't load processing state"
          error={processing.error}
          onRetry={() => void processing.refetch()}
        />
      ) : null}
      {!processing.isLoading && !processing.isError && processing.data ? (
        <dl className="tl-facts">
          <div>
            <dt>Status</dt>
            <dd>
              <StatusBadge
                tone={processingStatusTone(processing.data.status)}
                label={processingStatusLabel(processing.data.status)}
                pulse={processing.data.status === "PENDING"}
              />
            </dd>
          </div>
          <div>
            <dt>Source revision</dt>
            <dd className="tl-numeric">{processing.data.source_revision}</dd>
          </div>
          <div>
            <dt>Extraction revision</dt>
            <dd className="tl-numeric">
              {processing.data.extraction_revision ?? "No stored extraction"}
            </dd>
          </div>
          <div>
            <dt>Succeeded processing</dt>
            <dd className="tl-numeric">
              {processing.data.derived_revision === null
                ? "No succeeded processing yet"
                : `Revision ${processing.data.derived_revision}`}
            </dd>
          </div>
          <div>
            <dt>Indexed evidence</dt>
            <dd className="tl-numeric">
              {processing.data.semantic_revision === null
                ? "No indexed evidence yet"
                : `Revision ${processing.data.semantic_revision}`}
            </dd>
          </div>
          {processing.data.stale_mentions > 0 ? (
            <div>
              <dt>Outdated mentions</dt>
              <dd className="tl-numeric">{processing.data.stale_mentions}</dd>
            </div>
          ) : null}
          {!processing.data.worker_enabled ? (
            <div>
              <dt>Worker</dt>
              <dd>Off</dd>
            </div>
          ) : null}
        </dl>
      ) : null}
    </Section>
  );
}

function FactList({
  items,
  emptyTitle,
  emptyBody,
  renderMeta,
}: {
  items: Array<{ description: string; evidence?: { source_text: string | null } | null }>;
  emptyTitle: string;
  emptyBody: string;
  renderMeta?: (index: number) => React.ReactNode;
}): React.JSX.Element {
  if (items.length === 0) {
    return <EmptyState title={emptyTitle} body={emptyBody} />;
  }
  return (
    <ul className="tl-meeting-facts">
      {items.map((item, index) => (
        <li key={`${item.description}-${index}`}>
          <p className="tl-meeting-fact-title">{item.description}</p>
          {renderMeta ? <div className="tl-meeting-fact-meta">{renderMeta(index)}</div> : null}
          {item.evidence?.source_text ? (
            <blockquote className="tl-meeting-quote">{item.evidence.source_text}</blockquote>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function MeetingFactsSection({ meetingId }: { meetingId: string }): React.JSX.Element {
  const extraction = useMeetingExtraction(meetingId);
  return (
    <Section
      title="Facts"
      description="Decisions, tasks, issues, and risks exactly as extracted from this transcript."
    >
      {extraction.isLoading ? <SectionSkeleton label="Loading extracted facts" /> : null}
      {!extraction.isLoading && extraction.isError ? (
        <ErrorState
          title="Couldn't load extracted facts"
          error={extraction.error}
          onRetry={() => void extraction.refetch()}
        />
      ) : null}
      {!extraction.isLoading && !extraction.isError && !extraction.data?.has_extraction ? (
        <EmptyState
          title="No extraction is stored for this meeting yet."
          body="Facts will appear here after extraction succeeds. Refresh extraction to queue processing."
        />
      ) : null}
      {!extraction.isLoading && !extraction.isError && extraction.data?.extraction ? (
        <MeetingFactGroups extraction={extraction.data.extraction} />
      ) : null}
    </Section>
  );
}

function MeetingFactGroups({ extraction }: { extraction: ExtractionResponse }): React.JSX.Element {
  return (
    <>
      <Section title="Decisions">
        <FactList
          items={extraction.decisions}
          emptyTitle="No decisions extracted"
          emptyBody="The extraction found no explicitly stated decisions in this transcript."
        />
      </Section>
      <Section title="Tasks">
        <FactList
          items={extraction.tasks}
          emptyTitle="No tasks extracted"
          emptyBody="The extraction found no explicitly stated action items or commitments."
          renderMeta={(index) => {
            const task = extraction.tasks[index];
            return (
              <>
                <span>Owner: {task.owner?.trim() ? task.owner : "Not stated"}</span>
                <span>Deadline: {task.deadline?.trim() ? task.deadline : "Not stated"}</span>
              </>
            );
          }}
        />
        <p className="tl-meeting-note">
          Extracted commitments are read only. ThreadLine does not mark them complete here.
        </p>
      </Section>
      <Section title="Issues">
        <FactList
          items={extraction.issues}
          emptyTitle="No issues extracted"
          emptyBody="The extraction found no explicitly stated problems, blockers, or concerns."
        />
      </Section>
      <Section title="Risks">
        {extraction.risks.length === 0 ? (
          <EmptyState
            title="No risks extracted"
            body="The extraction found no explicitly raised risks in this transcript."
          />
        ) : (
          <ul className="tl-meeting-facts">
            {extraction.risks.map((risk, index) => (
              <li key={`${risk.description}-${index}`}>
                <p className="tl-meeting-fact-title">{risk.description}</p>
                <div className="tl-meeting-fact-meta">
                  <StatusBadge tone={extractionRiskTone(risk.severity)} label={riskSeverityLabel(risk.severity)} />
                </div>
                {risk.evidence?.source_text ? (
                  <blockquote className="tl-meeting-quote">{risk.evidence.source_text}</blockquote>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Section>
    </>
  );
}

export function MeetingEntitiesSection({ meetingId }: { meetingId: string }): React.JSX.Element {
  const mentions = useMeetingMentions(meetingId);
  const entities = useMeetingMentionEntities(mentions.data?.mentions);
  const ordered = orderedMeetingMentions(mentions.data?.mentions ?? []);
  const shown = ordered.slice(0, MEETING_MENTION_DISPLAY_LIMIT);
  const resolvedShown = shown.filter(
    (mention) => mention.entity_id !== null && mention.resolution_status === "RESOLVED",
  );

  return (
    <Section
      title="People and entities"
      description="Canonical entities linked only when a stored mention resolved to them."
    >
      {mentions.isLoading || (mentions.data && entities.isLoading) ? (
        <SectionSkeleton label="Loading linked entities" />
      ) : null}
      {!mentions.isLoading && mentions.isError ? (
        <ErrorState
          title="Couldn't load linked entities"
          error={mentions.error}
          onRetry={() => void mentions.refetch()}
        />
      ) : null}
      {!mentions.isLoading && !mentions.isError && ordered.length === 0 ? (
        <EmptyState
          title="No mentions recorded"
          body="No entity references have been stored for this meeting yet."
        />
      ) : null}
      {!mentions.isLoading && !mentions.isError && ordered.length > 0 ? (
        <>
          {!entities.isError && resolvedShown.length > 0 ? (
            <ul className="tl-meeting-facts">
              {resolvedShown.map((mention: MeetingMention) => (
                <li key={mention.mention_id}>
                  <p className="tl-meeting-fact-title">
                    <Link
                      className="tl-meeting-entity-link"
                      to={entityHref(mention.entity_id as string)}
                    >
                      {entityDisplayName(entities.directory, mention.entity_id as string)}
                    </Link>
                  </p>
                  <div className="tl-meeting-fact-meta">
                    <Badge>{mention.entity_type === "PERSON" ? "Person" : "Issue"}</Badge>
                    <span>Observed as “{mention.text}”</span>
                  </div>
                  <blockquote className="tl-meeting-quote">{mention.source_text}</blockquote>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              title="No linked entities yet"
              body="ThreadLine links an entity only after a stored mention resolves to it. Unresolved references stay visible as transcript evidence."
            />
          )}
          {ordered.length > shown.length ? (
            <p className="tl-meeting-note">
              Showing {shown.length} of {ordered.length} stored mentions.
            </p>
          ) : null}
          {entities.totalResolved > entities.shown ? (
            <p className="tl-meeting-note">
              Showing names for {entities.shown} of {entities.totalResolved} linked entities.
            </p>
          ) : null}
        </>
      ) : null}
    </Section>
  );
}

export function MeetingDependenciesSection({ meetingId }: { meetingId: string }): React.JSX.Element {
  const mentions = useMeetingMentions(meetingId);
  const entities = useMeetingMentionEntities(mentions.data?.mentions);
  const focalIds = useMemo(
    () => (mentions.data ? uniqueResolvedEntityIds(mentions.data.mentions) : []),
    [mentions.data],
  );
  const dependencies = useMeetingDependencies(meetingId, focalIds);
  const counterpartIds = useMemo(
    () =>
      Array.from(
        new Set(
          dependencies.edges.flatMap((edge) => [edge.source_entity_id, edge.target_entity_id]),
        ),
      ).sort(),
    [dependencies.edges],
  );
  const names = useMeetingEntityNames(counterpartIds, MEETING_DEPENDENCY_NAME_LIMIT);

  if (!mentions.isLoading && !mentions.isError && focalIds.length === 0) return <></>;
  return (
    <Section
      title="Dependencies"
      description="Only explicit DEPENDS_ON and BLOCKS relationships whose evidence includes this meeting."
    >
      {mentions.isLoading || dependencies.isLoading ? (
        <SectionSkeleton label="Loading dependencies" />
      ) : null}
      {!mentions.isLoading && !dependencies.isLoading && mentions.isError ? (
        <ErrorState
          title="Couldn't load linked entities"
          error={mentions.error}
          onRetry={() => void mentions.refetch()}
        />
      ) : null}
      {!mentions.isLoading &&
      !mentions.isError &&
      !dependencies.isLoading &&
      dependencies.isError ? (
        <ErrorState
          title="Couldn't load dependencies"
          error={new Error("One or more dependency lookups failed.")}
          onRetry={() => dependencies.refetch()}
        />
      ) : null}
      {!mentions.isLoading &&
      !mentions.isError &&
      !dependencies.isLoading &&
      !dependencies.isError &&
      dependencies.edges.length === 0 ? (
        <EmptyState
          title="No explicit dependencies"
          body="This meeting's linked entities have no DEPENDS_ON or BLOCKS relationships attributed to it."
        />
      ) : null}
      {!mentions.isLoading &&
      !mentions.isError &&
      !dependencies.isLoading &&
      !dependencies.isError &&
      dependencies.edges.length > 0 ? (
        <>
          <ul className="tl-meeting-facts">
            {dependencies.edges.map((edge) => {
              const sourceName = names.directory.get(edge.source_entity_id) ??
                entities.directory.get(edge.source_entity_id) ??
                `Entity ${shortId(edge.source_entity_id)}`;
              const targetName = names.directory.get(edge.target_entity_id) ??
                entities.directory.get(edge.target_entity_id) ??
                `Entity ${shortId(edge.target_entity_id)}`;
              return (
                <li key={edge.relationship_id}>
                  <p className="tl-meeting-fact-title">
                    <Link className="tl-meeting-entity-link" to={entityHref(edge.source_entity_id)}>
                      {sourceName}
                    </Link>{" "}
                    {relationshipPhrase(edge.relationship_type)}{" "}
                    <Link className="tl-meeting-entity-link" to={entityHref(edge.target_entity_id)}>
                      {targetName}
                    </Link>
                  </p>
                  <div className="tl-meeting-fact-meta">
                    <StatusBadge
                      tone={edge.relationship_type === "BLOCKS" ? "danger" : "warning"}
                      label={relationshipPhrase(edge.relationship_type)}
                    />
                    <span>{edge.evidence}</span>
                  </div>
                  {edge.source_text ? (
                    <blockquote className="tl-meeting-quote">{edge.source_text}</blockquote>
                  ) : null}
                </li>
              );
            })}
          </ul>
          {names.isLoading ? <p className="tl-meeting-note">Resolving related entity names…</p> : null}
        </>
      ) : null}
    </Section>
  );
}

export function MeetingChangesSection({ meetingId }: { meetingId: string }): React.JSX.Element {
  const changes = useMeetingChanges(meetingId);
  const changeIds = meetingChangeEntities(changes.data?.changes);
  const names = useMeetingEntityNames(changeIds, MEETING_DEPENDENCY_NAME_LIMIT);

  return (
    <Section
      title="Related changes"
      description="Organisation changes attributed to this meeting by ThreadLine intelligence."
    >
      {changes.isLoading || (changes.data && names.isLoading) ? (
        <SectionSkeleton label="Loading related changes" />
      ) : null}
      {!changes.isLoading && changes.isError ? (
        <ErrorState
          title="Couldn't load related changes"
          error={changes.error}
          onRetry={() => void changes.refetch()}
        />
      ) : null}
      {!changes.isLoading && !changes.isError && (changes.data?.changes.length ?? 0) === 0 ? (
        <EmptyState
          title="No changes attributed to this meeting"
          body="State transitions, escalations, dependencies, and impacts connected to this meeting will appear here."
        />
      ) : null}
      {!changes.isLoading && !changes.isError && (changes.data?.changes.length ?? 0) > 0 ? (
        <ul className="tl-meeting-facts">
          {(changes.data?.changes ?? []).map((change) => (
            <li key={change.change_id}>
              <p className="tl-meeting-fact-title">
                <Link className="tl-meeting-entity-link" to={entityHref(change.entity_id)}>
                  {entityDisplayName(names.directory, change.entity_id)}
                </Link>
              </p>
              <div className="tl-meeting-fact-meta">
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
                  label={changeTypeLabel(change.change_type)}
                />
                <span>{change.evidence}</span>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </Section>
  );
}

export function MeetingTranscriptSection({ transcript }: { transcript: string }): React.JSX.Element {
  return (
    <Section title="Source transcript" description="The authoritative source text for this meeting.">
      <details className="tl-meeting-transcript">
        <summary>Read full transcript ({transcriptSizeLabel(transcript)})</summary>
        <p className="tl-transcript tl-meeting-transcript-body">{transcript}</p>
      </details>
    </Section>
  );
}

export function MeetingMetadataRail({
  meeting,
  processing,
}: {
  meeting: MeetingResponse;
  processing: MeetingProcessingResponse | undefined;
}): React.JSX.Element {
  const participants = participantPreview(meeting.participants, meeting.participants.length);
  return (
    <aside className="tl-meeting-rail" aria-label="Meeting details">
      <Card>
        <dl className="tl-facts">
          <div>
            <dt>Date</dt>
            <dd className="tl-numeric">{formatMeetingDateTime(meeting.meeting_date) ?? "Date unknown"}</dd>
          </div>
          <div>
            <dt>Ingested</dt>
            <dd className="tl-numeric">{formatMeetingDay(meeting.ingested_at) ?? "Date unknown"}</dd>
          </div>
          <div>
            <dt>Participants</dt>
            <dd>
              {meeting.participants.length === 0 ? (
                "No participants listed"
              ) : (
                <span className="tl-badge-row">
                  {participants.shown.map((name) => (
                    <Badge key={name}>{name}</Badge>
                  ))}
                  {participants.remaining > 0 ? <Badge>+{participants.remaining} more</Badge> : null}
                </span>
              )}
            </dd>
          </div>
          <div>
            <dt>Source revision</dt>
            <dd className="tl-numeric">{meeting ? processing?.source_revision ?? "Unknown" : "Unknown"}</dd>
          </div>
          <div>
            <dt>Processing</dt>
            <dd>
              {processing ? processingStatusLabel(processing.status) : "Unknown"}
              {processing && !processing.worker_enabled ? " · worker off" : ""}
            </dd>
          </div>
          <div>
            <dt>Meeting ID</dt>
            <dd className="tl-mono">{meeting.meeting_id}</dd>
          </div>
        </dl>
      </Card>
    </aside>
  );
}
