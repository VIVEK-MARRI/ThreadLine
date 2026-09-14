/* Entity feature API. */

import { request } from "./client";
import type {
  CreateEntityRequest,
  EntityResponse,
  EntityType,
  RegisterMentionRequest,
  RegisterMentionResponse,
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
