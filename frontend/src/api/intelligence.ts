/* Intelligence + query feature APIs. */

import { request } from "./client";
import type {
  AttentionResponse,
  ChangesResponse,
  DependencyGraphResponse,
  PortfolioResponse,
} from "../types/intelligence";
import type { QueryEvidenceResponse, QueryRequest, QueryResponse } from "../types/query";
import type { Diagnostics, JobHealth } from "../types/jobs";

export const intelligenceApi = {
  attention(): Promise<AttentionResponse> {
    return request<AttentionResponse>("/api/v1/attention");
  },

  portfolio(): Promise<PortfolioResponse> {
    return request<PortfolioResponse>("/api/v1/portfolio");
  },

  changes(params?: {
    change_type?: string;
    severity?: string;
    entity_id?: string;
    limit?: number;
  }): Promise<ChangesResponse> {
    return request<ChangesResponse>("/api/v1/changes", { query: params });
  },

  dependencyGraph(entityId: string, maxDepth = 3): Promise<DependencyGraphResponse> {
    return request<DependencyGraphResponse>(
      `/api/v1/entities/${encodeURIComponent(entityId)}/dependency-graph`,
      { query: { max_depth: maxDepth } },
    );
  },
};

export const queryApi = {
  ask(payload: QueryRequest): Promise<QueryResponse> {
    return request<QueryResponse>("/api/v1/query", { method: "POST", body: payload });
  },

  evidence(payload: QueryRequest): Promise<QueryEvidenceResponse> {
    return request<QueryEvidenceResponse>("/api/v1/query/evidence", {
      method: "POST",
      body: payload,
    });
  },
};

export const jobsApi = {
  health(): Promise<JobHealth> {
    return request<JobHealth>("/api/v1/health/jobs");
  },

  diagnostics(): Promise<Diagnostics> {
    return request<Diagnostics>("/health/diagnostics");
  },
};
