/* Meeting workspace data layer.
 *
 * Every read is tenant-scoped and backed by the central API client. List and
 * detail queries stay independent; bounded point lookups join entity names
 * and explicit dependencies without N+1 explosions. New meetings and
 * extraction refreshes invalidate the affected tenant namespaces.
 */

import { useMemo } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { entitiesApi } from "../../api/entities";
import { intelligenceApi } from "../../api/intelligence";
import { queryKeys } from "../../api/keys";
import { meetingsApi } from "../../api/meetings";
import { useOrganisation } from "../../auth/OrganisationContext";
import type { EntityRelationship } from "../../types/entities";
import type {
  ChangesResponse,
  OrganisationChange,
} from "../../types/intelligence";
import type {
  MeetingExtractionResponse,
  MeetingListResponse,
  MeetingMention,
  MeetingMentionsResponse,
  MeetingProcessingResponse,
  MeetingResponse,
} from "../../types/meetings";
import { uniqueResolvedEntityIds } from "./meetingsFormat";

export const MEETING_ENTITY_NAME_LIMIT = 12;
export const MEETING_DEPENDENCY_SOURCE_LIMIT = 3;
export const MEETING_DEPENDENCY_NAME_LIMIT = 12;
export const MEETING_CHANGES_LIMIT = 20;
const MEETING_PROCESSING_POLL_MS = 5_000;

export function useMeetingList(limit: number) {
  const { organisationId } = useOrganisation();
  return useQuery<MeetingListResponse>({
    queryKey: queryKeys.meetings(organisationId ?? "", { limit }),
    queryFn: () => meetingsApi.list(limit),
    enabled: Number.isFinite(limit) && limit > 0 && Boolean(organisationId),
    staleTime: 15_000,
  });
}

export function useMeetingDetail(meetingId: string) {
  const { organisationId } = useOrganisation();
  return useQuery<MeetingResponse>({
    queryKey: queryKeys.meeting(organisationId ?? "", meetingId),
    queryFn: () => meetingsApi.get(meetingId),
    enabled: Boolean(organisationId && meetingId),
    staleTime: 30_000,
  });
}

export function useMeetingExtraction(meetingId: string) {
  const { organisationId } = useOrganisation();
  return useQuery<MeetingExtractionResponse>({
    queryKey: queryKeys.meetingSection(organisationId ?? "", meetingId, "extraction"),
    queryFn: () => meetingsApi.getExtraction(meetingId),
    enabled: Boolean(organisationId && meetingId),
    staleTime: 30_000,
  });
}

export function useMeetingProcessing(meetingId: string) {
  const { organisationId } = useOrganisation();
  return useQuery<MeetingProcessingResponse>({
    queryKey: queryKeys.meetingSection(organisationId ?? "", meetingId, "processing"),
    queryFn: () => meetingsApi.getProcessing(meetingId),
    enabled: Boolean(organisationId && meetingId),
    staleTime: 5_000,
    // Poll only while queued work can still resolve itself. A disabled
    // worker, completed run, failure, or stale revision needs user input.
    refetchInterval: (current) =>
      current.state.data?.status === "PENDING" &&
      current.state.data?.worker_enabled !== false
        ? MEETING_PROCESSING_POLL_MS
        : false,
  });
}

export function useMeetingMentions(meetingId: string) {
  const { organisationId } = useOrganisation();
  return useQuery<MeetingMentionsResponse>({
    queryKey: queryKeys.meetingSection(organisationId ?? "", meetingId, "mentions"),
    queryFn: () => meetingsApi.listMentions(meetingId),
    enabled: Boolean(organisationId && meetingId),
    staleTime: 30_000,
  });
}

export function useMeetingChanges(meetingId: string) {
  const { organisationId } = useOrganisation();
  const filters = useMemo(
    () => ({ meeting_id: meetingId, limit: MEETING_CHANGES_LIMIT }),
    [meetingId],
  );
  return useQuery<ChangesResponse>({
    queryKey: queryKeys.changes(organisationId ?? "", filters),
    queryFn: () => intelligenceApi.changes(filters),
    enabled: Boolean(organisationId && meetingId),
    staleTime: 30_000,
  });
}

export interface MeetingEntityDirectory {
  directory: Map<string, string>;
  totalResolved: number;
  shown: number;
  isLoading: boolean;
  isError: boolean;
  refetch: () => void;
}

/**
 * Resolve canonical names for a bounded set of entity ids. Results use the
 * standard per-entity tenant keys, so detail, dependency, and change rows
 * share one cache entry per entity.
 */
export function useMeetingEntityNames(
  entityIds: string[],
  limit: number = MEETING_ENTITY_NAME_LIMIT,
): MeetingEntityDirectory {
  const { organisationId } = useOrganisation();
  const selected = useMemo(
    () => Array.from(new Set(entityIds.filter(Boolean))).sort().slice(0, Math.max(0, limit)),
    [entityIds, limit],
  );
  const results = useQueries({
    queries: selected.map((entityId) => ({
      queryKey: queryKeys.entity(organisationId ?? "", entityId),
      queryFn: () => entitiesApi.get(entityId),
      enabled: Boolean(organisationId && entityId),
      staleTime: 60_000,
      retry: 1,
    })),
  });
  const directory = useMemo(() => {
    const names = new Map<string, string>();
    results.forEach((result, index) => {
      const name = result.data?.canonical_name?.trim();
      if (name) names.set(selected[index], name);
    });
    return names;
  }, [results, selected]);

  return {
    directory,
    totalResolved: new Set(entityIds.filter(Boolean)).size,
    shown: selected.length,
    isLoading: results.some((result) => result.isLoading),
    isError: results.some((result) => result.isError),
    refetch: () => {
      void Promise.all(results.map((result) => result.refetch()));
    },
  };
}

export function useMeetingMentionEntities(mentions: MeetingMention[] | undefined) {
  const resolvedIds = useMemo(
    () => (mentions ? uniqueResolvedEntityIds(mentions) : []),
    [mentions],
  );
  return useMeetingEntityNames(resolvedIds, MEETING_ENTITY_NAME_LIMIT);
}

export interface MeetingDependencyEdge extends EntityRelationship {
  otherEntityId: string;
}

export interface MeetingDependencies {
  edges: MeetingDependencyEdge[];
  sourceCount: number;
  sourceShown: number;
  isLoading: boolean;
  isError: boolean;
  refetch: () => void;
}

/**
 * Explicit dependencies involving a bounded set of meeting entities. Every
 * relationship is already tenant-scoped server-side; the client only keeps
 * edges whose evidence includes this meeting.
 */
export function useMeetingDependencies(
  meetingId: string,
  entityIds: string[],
  sourceLimit: number = MEETING_DEPENDENCY_SOURCE_LIMIT,
): MeetingDependencies {
  const { organisationId } = useOrganisation();
  const sources = useMemo(
    () =>
      Array.from(new Set(entityIds.filter(Boolean)))
        .sort()
        .slice(0, Math.max(0, sourceLimit)),
    [entityIds, sourceLimit],
  );
  const results = useQueries({
    queries: sources.map((entityId) => ({
      queryKey: queryKeys.entitySection(organisationId ?? "", entityId, "dependencies"),
      queryFn: () => entitiesApi.dependencies(entityId),
      enabled: Boolean(organisationId && meetingId && entityId),
      staleTime: 60_000,
      retry: 1,
    })),
  });

  const edges = useMemo(() => {
    const seen = new Map<string, MeetingDependencyEdge>();
    for (const result of results) {
      for (const relationship of result.data ?? []) {
        if (!relationship.related_meeting_ids.includes(meetingId)) continue;
        if (seen.has(relationship.relationship_id)) continue;
        const otherEntityId =
          relationship.source_entity_id === relationship.target_entity_id
            ? relationship.source_entity_id
            : sources.includes(relationship.source_entity_id)
              ? relationship.target_entity_id
              : relationship.source_entity_id;
        seen.set(relationship.relationship_id, { ...relationship, otherEntityId });
      }
    }
    return Array.from(seen.values()).sort((first, second) =>
      first.deterministic_sort_key.localeCompare(second.deterministic_sort_key),
    );
  }, [results, meetingId, sources]);

  return {
    edges,
    sourceCount: new Set(entityIds.filter(Boolean)).size,
    sourceShown: sources.length,
    isLoading: results.some((result) => result.isLoading),
    isError: results.some((result) => result.isError),
    refetch: () => {
      void Promise.all(results.map((result) => result.refetch()));
    },
  };
}

export function meetingChangeEntities(changes: OrganisationChange[] | undefined): string[] {
  return Array.from(
    new Set((changes ?? []).map((change) => change.entity_id).filter(Boolean)),
  ).sort();
}
