/* Entity + resolution contracts. Shapes verified against backend OpenAPI. */

export type EntityType = "PERSON" | "ISSUE";
export type ResolutionStatus = "RESOLVED" | "UNRESOLVED" | "AMBIGUOUS";

export interface EntityResponse {
  entity_id: string;
  entity_type: EntityType;
  canonical_name: string;
  aliases?: string[];
  created_at: string;
}

export interface CreateEntityRequest {
  entity_type: EntityType;
  canonical_name: string;
}

export interface RegisterMentionRequest {
  entity_type: EntityType;
  text: string;
  meeting_id: string;
  source_text: string;
}

export interface RegisterMentionResponse {
  mention_id: string;
  text: string;
  entity_type: EntityType;
  entity_id: string | null;
  resolution_status: ResolutionStatus;
  created_at: string;
}

export interface EntityCandidate {
  entity_id: string;
  canonical_name: string;
  score?: number;
}

export interface ResolutionDecision {
  mention_id: string;
  outcome: ResolutionStatus;
  entity_id: string | null;
  reason?: string;
}

export type EntityRelationshipType = "CO_OCCURS_WITH" | "DEPENDS_ON" | "BLOCKS" | "RELATED_TO";

export interface EntityRelationship {
  relationship_id: string;
  source_entity_id: string;
  target_entity_id: string;
  relationship_type: EntityRelationshipType;
  evidence_type: string;
  evidence: string;
  related_meeting_ids: string[];
  source_text: string | null;
  mention_id: string | null;
  strength: number;
  deterministic_sort_key: string;
}
