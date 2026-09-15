/* Entity workspace data layer.
 *
 * Every read is tenant-scoped and backed by the central API client. The
 * directory joins the canonical registry with the portfolio intelligence
 * snapshot; detail sections stay independent and fail independently. Bounded
 * name joins reuse the standard per-entity tenant keys and never fan out
 * across an unbounded id list.
 */

import { useMemo } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { entitiesApi } from "../../api/entities";
import { intelligenceApi } from "../../api/intelligence";
import { queryKeys } from "../../api/keys";
import { useOrganisation } from "../../auth/OrganisationContext";
import type { ChangesResponse, PortfolioEntitySummary } from "../../types/intelligence";
import type {
  CreateEntityRequest,
  DependencyGraphResponse,
  EntityActionsResponse,
  EntityAttentionResponse,
  EntityImpactResponse,
  EntityInsightsResponse,
  EntityMemoryResponse,
  EntityRelationshipGraph,
  EntityResponse,
  EntityTemporalResponse,
  EntityType,
  UnifiedEntityTimelineResponse,
} from "../../types/entities";
import { combineEntityDirectory, type EntityDirectoryRow } from "./entitiesFormat";

export const ENTITY_CHANGES_LIMIT = 20;
export const ENTITY_NAME_JOIN_LIMIT = 16;
export const ENTITY_DEPENDENCY_GRAPH_DEPTH = 3;
export const ENTITY_IMPACT_GRAPH_DEPTH = 3;
export const ENTITY_RELATED_MEETING_LIMIT = 8;

export function useEntityList(entityType: EntityType | null) {
  const { organisationId } = useOrganisation();
  return useQuery<EntityResponse[]>({
    queryKey: queryKeys.entities(organisationId ?? "", entityType),
    queryFn: () => entitiesApi.list(entityType),
    enabled: Boolean(organisationId),
    staleTime: 30_000,
  });
}

export function useEntityPortfolio() {
  const { organisationId } = useOrganisation();
  return useQuery<{ entities: PortfolioEntitySummary[] }>({
    queryKey: queryKeys.portfolio(organisationId ?? ""),
    queryFn: () => intelligenceApi.portfolio(),
    enabled: Boolean(organisationId),
    staleTime: 30_000,
  });
}

export interface EntityDirectoryQueries {
  rows: EntityDirectoryRow[];
  assessedCount: number;
  listPending: boolean;
  listError: unknown;
  portfolioError: unknown;
  refetchList: () => void;
  refetchPortfolio: () => void;
}

export function useEntityDirectoryRows(entityType: EntityType | null): EntityDirectoryQueries {
  const list = useEntityList(entityType);
  const portfolio = useEntityPortfolio();
  const rows = useMemo(
    () => combineEntityDirectory(list.data ?? [], portfolio.data?.entities ?? []),
    [list.data, portfolio.data],
  );
  const assessedCount = useMemo(
    () => rows.filter((row) => row.summary !== null).length,
    [rows],
  );
  return {
    rows,
    assessedCount,
    listPending: list.isPending || portfolio.isPending,
    listError: list.error ?? null,
    portfolioError: portfolio.error ?? null,
    refetchList: () => {
      void list.refetch();
    },
    refetchPortfolio: () => {
      void portfolio.refetch();
    },
  };
}

export function useRecordEntity() {
  const { organisationId } = useOrganisation();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateEntityRequest) => entitiesApi.create(payload),
    onSuccess: () => {
      if (!organisationId) return;
      void queryClient.invalidateQueries({ queryKey: ["tl", organisationId, "entities"] });
      void queryClient.invalidateQueries({ queryKey: queryKeys.portfolio(organisationId) });
    },
  });
}

export function useEntityDetail(entityId: string) {
  const { organisationId } = useOrganisation();
  return useQuery<EntityResponse>({
    queryKey: queryKeys.entity(organisationId ?? "", entityId),
    queryFn: () => entitiesApi.get(entityId),
    enabled: Boolean(organisationId && entityId),
    staleTime: 30_000,
  });
}

export function useEntityTemporal(entityId: string) {
  const { organisationId } = useOrganisation();
  return useQuery<EntityTemporalResponse>({
    queryKey: queryKeys.entitySection(organisationId ?? "", entityId, "temporal"),
    queryFn: () => entitiesApi.temporal(entityId),
    enabled: Boolean(organisationId && entityId),
    staleTime: 30_000,
  });
}

export function useEntityUnifiedTimeline(entityId: string) {
  const { organisationId } = useOrganisation();
  return useQuery<UnifiedEntityTimelineResponse>({
    queryKey: queryKeys.entitySection(organisationId ?? "", entityId, "timeline"),
    queryFn: () => entitiesApi.unifiedTimeline(entityId),
    enabled: Boolean(organisationId && entityId),
    staleTime: 30_000,
  });
}

export function useEntityMemory(entityId: string) {
  const { organisationId } = useOrganisation();
  return useQuery<EntityMemoryResponse>({
    queryKey: queryKeys.entitySection(organisationId ?? "", entityId, "memory"),
    queryFn: () => entitiesApi.memory(entityId),
    enabled: Boolean(organisationId && entityId),
    staleTime: 30_000,
  });
}

export function useEntityInsights(entityId: string) {
  const { organisationId } = useOrganisation();
  return useQuery<EntityInsightsResponse>({
    queryKey: queryKeys.entitySection(organisationId ?? "", entityId, "insights"),
    queryFn: () => entitiesApi.insights(entityId),
    enabled: Boolean(organisationId && entityId),
    staleTime: 30_000,
  });
}

export function useEntityAttention(entityId: string) {
  const { organisationId } = useOrganisation();
  return useQuery<EntityAttentionResponse>({
    queryKey: queryKeys.entitySection(organisationId ?? "", entityId, "attention"),
    queryFn: () => entitiesApi.attention(entityId),
    enabled: Boolean(organisationId && entityId),
    staleTime: 30_000,
  });
}

export function useEntityActions(entityId: string) {
  const { organisationId } = useOrganisation();
  return useQuery<EntityActionsResponse>({
    queryKey: queryKeys.entitySection(organisationId ?? "", entityId, "actions"),
    queryFn: () => entitiesApi.actions(entityId),
    enabled: Boolean(organisationId && entityId),
    staleTime: 30_000,
  });
}

export function useEntityRelationships(entityId: string) {
  const { organisationId } = useOrganisation();
  return useQuery<EntityRelationshipGraph>({
    queryKey: queryKeys.entitySection(organisationId ?? "", entityId, "relationships"),
    queryFn: () => entitiesApi.relationships(entityId),
    enabled: Boolean(organisationId && entityId),
    staleTime: 30_000,
  });
}

export function useEntityDependencyGraph(entityId: string) {
  const { organisationId } = useOrganisation();
  const filters = useMemo(
    () => ({ max_depth: ENTITY_DEPENDENCY_GRAPH_DEPTH }),
    [],
  );
  return useQuery<DependencyGraphResponse>({
    queryKey: queryKeys.entitySection(organisationId ?? "", entityId, "dependency-graph"),
    queryFn: () => entitiesApi.dependencyGraph(entityId, filters.max_depth),
    enabled: Boolean(organisationId && entityId),
    staleTime: 30_000,
  });
}

export function useEntityImpacts(entityId: string) {
  const { organisationId } = useOrganisation();
  const filters = useMemo(() => ({ max_depth: ENTITY_IMPACT_GRAPH_DEPTH }), []);
  return useQuery<EntityImpactResponse>({
    queryKey: queryKeys.entitySection(organisationId ?? "", entityId, "impacts"),
    queryFn: () => entitiesApi.impacts(entityId, filters.max_depth),
    enabled: Boolean(organisationId && entityId),
    staleTime: 30_000,
  });
}

export function useEntityChanges(entityId: string) {
  const { organisationId } = useOrganisation();
  const filters = useMemo(
    () => ({ entity_id: entityId, limit: ENTITY_CHANGES_LIMIT }),
    [entityId],
  );
  return useQuery<ChangesResponse>({
    queryKey: queryKeys.changes(organisationId ?? "", filters),
    queryFn: () => intelligenceApi.changes(filters),
    enabled: Boolean(organisationId && entityId),
    staleTime: 30_000,
  });
}

export interface WorkspaceEntityDirectory {
  directory: Map<string, string>;
  totalRequested: number;
  shown: number;
  isLoading: boolean;
  isError: boolean;
  refetch: () => void;
}

/**
 * Resolve canonical names for a bounded set of entity ids under the standard
 * per-entity tenant keys. Used by dependency, impact, and change rows.
 */
export function useWorkspaceEntityNames(
  entityIds: string[],
  limit: number = ENTITY_NAME_JOIN_LIMIT,
): WorkspaceEntityDirectory {
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
    totalRequested: new Set(entityIds.filter(Boolean)).size,
    shown: selected.length,
    isLoading: results.some((result) => result.isLoading),
    isError: results.some((result) => result.isError),
    refetch: () => {
      void Promise.all(results.map((result) => result.refetch()));
    },
  };
}
