/* Natural-language query contracts. Shapes verified against backend OpenAPI. */

export type QueryIntent =
  | "ENTITY_STATUS"
  | "ENTITY_HISTORY"
  | "ENTITY_RISKS"
  | "ENTITY_DEPENDENCIES"
  | "ENTITY_IMPACTS"
  | "ENTITY_ACTIONS"
  | "ENTITY_CHANGES"
  | "ORGANISATION_PRIORITIES"
  | "ORGANISATION_CHANGES"
  | "ORGANISATION_RISKS"
  | "UNKNOWN";

export interface EvidenceItem {
  evidence_id: string;
  evidence_type: string;
  entity_id: string | null;
  meeting_id: string | null;
  mention_id: string | null;
  source_text: string | null;
  timestamp: string | null;
  summary: string;
  severity_weight: number;
  metadata: Record<string, unknown>;
  source_reference: string | null;
}

export interface QueryRequest {
  question: string;
  entity_id?: string | null;
  max_evidence_items?: number;
  include_source_text?: boolean;
}

export interface QueryResponse {
  query_id: string;
  question: string;
  intent: QueryIntent;
  entity_id: string | null;
  answer: string;
  evidence: EvidenceItem[];
  cited_evidence_ids: string[];
  insufficient_evidence: boolean;
  warnings: string[];
  generated_at: string | null;
}

export interface QueryEvidenceResponse {
  query_id: string;
  question: string;
  intent: QueryIntent;
  entity_id: string | null;
  evidence: EvidenceItem[];
}
