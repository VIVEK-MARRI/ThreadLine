/* Entity feature API. */

import { request } from "./client";
import type {
  CreateEntityRequest,
  DependencyGraphResponse,
  EntityActionsResponse,
  EntityAttentionResponse,
  EntityCorrelationResponse,
  EntityImpactResponse,
  EntityInsightsResponse,
  EntityMemoryResponse,
  EntityRelationship,
  EntityRelationshipGraph,
  EntityResponse,
  EntityTemporalResponse,
  EntityType,
  RegisterMentionRequest,
  RegisterMentionResponse,
  UnifiedEntityTimelineResponse,
} from "../types/entities";

export const entitiesApi = {
  list(entityType?: EntityType | null): Promise<EntityResponse[]> {
    return request<EntityResponse[]>("/api/v1/entities", {
      query: entityType ? { entity_type: entityType } : undefined,
    });
  },

  get(entityId: string): Promise<EntityResponse> {
    return request<EntityResponse>(`/api/v1/entities/${encodeURIComponent(entityId)}`);
  },

  dependencies(entityId: string): Promise<EntityRelationship[]> {
    return request<EntityRelationship[]>(
      `/api/v1/entities/${encodeURIComponent(entityId)}/dependencies`,
    );
  },

  relationships(entityId: string): Promise<EntityRelationshipGraph> {
    return request<EntityRelationshipGraph>(
      `/api/v1/entities/${encodeURIComponent(entityId)}/relationships`,
    );
  },

  temporal(entityId: string): Promise<EntityTemporalResponse> {
    return request<EntityTemporalResponse>(
      `/api/v1/entities/${encodeURIComponent(entityId)}/temporal`,
    );
  },

  unifiedTimeline(entityId: string): Promise<UnifiedEntityTimelineResponse> {
    return request<UnifiedEntityTimelineResponse>(
      `/api/v1/entities/${encodeURIComponent(entityId)}/timeline`,
    );
  },

  memory(entityId: string): Promise<EntityMemoryResponse> {
    return request<EntityMemoryResponse>(
      `/api/v1/entities/${encodeURIComponent(entityId)}/memory`,
    );
  },

  correlations(entityId: string): Promise<EntityCorrelationResponse> {
    return request<EntityCorrelationResponse>(
      `/api/v1/entities/${encodeURIComponent(entityId)}/correlations`,
    );
  },

  insights(entityId: string): Promise<EntityInsightsResponse> {
    return request<EntityInsightsResponse>(
      `/api/v1/entities/${encodeURIComponent(entityId)}/insights`,
    );
  },

  attention(entityId: string): Promise<EntityAttentionResponse> {
    return request<EntityAttentionResponse>(
      `/api/v1/entities/${encodeURIComponent(entityId)}/attention`,
    );
  },

  actions(entityId: string): Promise<EntityActionsResponse> {
    return request<EntityActionsResponse>(
      `/api/v1/entities/${encodeURIComponent(entityId)}/actions`,
    );
  },

  dependencyGraph(
    entityId: string,
    maxDepth = 3,
  ): Promise<DependencyGraphResponse> {
    return request<DependencyGraphResponse>(
      `/api/v1/entities/${encodeURIComponent(entityId)}/dependency-graph`,
      { query: { max_depth: maxDepth } },
    );
  },

  impacts(entityId: string, maxDepth = 3): Promise<EntityImpactResponse> {
    return request<EntityImpactResponse>(
      `/api/v1/entities/${encodeURIComponent(entityId)}/impacts`,
      { query: { max_depth: maxDepth } },
    );
  },

  create(payload: CreateEntityRequest): Promise<EntityResponse> {
    return request<EntityResponse>("/api/v1/entities", { method: "POST", body: payload });
  },

  registerMention(payload: RegisterMentionRequest): Promise<RegisterMentionResponse> {
    return request<RegisterMentionResponse>("/api/v1/entities/mentions", {
      method: "POST",
      body: payload,
    });
  },

  /** Raw section fetch for entity sub-resources (insights, timeline, ...). */
  section<T>(entityId: string, section: string): Promise<T> {
    return request<T>(
      `/api/v1/entities/${encodeURIComponent(entityId)}/${encodeURIComponent(section)}`,
    );
  },
};
