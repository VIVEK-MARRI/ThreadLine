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

export interface EntityRelationshipGraph {
  entity_id: string;
  relationship_count: number;
  related_entity_ids: string[];
  relationships: EntityRelationship[];
}

export type TemporalState = "UNKNOWN" | "OPEN" | "IN_PROGRESS" | "BLOCKED" | "RESOLVED";

export interface StateObservation {
  observation_index: number;
  meeting_id: string;
  meeting_title: string;
  meeting_date: string;
  mention_id: string;
  evidence_text: string;
  interpreted_state: TemporalState;
  transition_occurred: boolean;
  from_state: TemporalState;
  to_state: TemporalState;
  is_valid_transition: boolean;
  transition_skipped_reason: string | null;
}

export interface EntityTemporalResponse {
  entity_id: string;
  canonical_name: string;
  entity_type: EntityType;
  current_state: TemporalState;
  observation_count: number;
  transition_count: number;
  timeline: StateObservation[];
}

export interface EntityObservation {
  meeting_id: string;
  meeting_title: string;
  meeting_date: string;
  mention_id: string;
  mention_text: string;
  source_text: string;
}

export interface EntityCorrelationResponse {
  entity_id: string;
  canonical_name: string;
  entity_type: EntityType;
  observation_count: number;
  observations: EntityObservation[];
}

export type TimelineEventType =
  | "OBSERVATION"
  | "STATE_CHANGE"
  | "MEMORY_FACT"
  | "INSIGHT"
  | "ATTENTION"
  | "ACTION";

export interface TimelineEvent {
  event_id: string;
  entity_id: string;
  event_type: TimelineEventType;
  occurred_at: string;
  related_meeting_id: string | null;
  title: string;
  description: string;
  event_metadata: Record<string, unknown>;
}

export interface UnifiedEntityTimelineResponse {
  entity_id: string;
  first_observed_at: string | null;
  last_observed_at: string | null;
  event_count: number;
  events: TimelineEvent[];
}

export type MemoryFactType =
  | "FIRST_OBSERVED"
  | "LAST_OBSERVED"
  | "CURRENT_STATE"
  | "STATE_TRANSITION"
  | "REPEATED_OBSERVATION";

export interface EntityMemoryFact {
  fact_type: MemoryFactType;
  value: string;
  source_meeting_id: string | null;
  source_mention_id: string | null;
  observed_at: string | null;
  detail: string | null;
}

export interface EntityMemoryResponse {
  entity_id: string;
  canonical_name: string;
  entity_type: EntityType;
  first_observed_at: string | null;
  last_observed_at: string | null;
  meeting_count: number;
  observation_count: number;
  current_state: TemporalState;
  facts: EntityMemoryFact[];
}

export type InsightType =
  | "UNKNOWN_STATE"
  | "STATE_CHANGED"
  | "ISSUE_BLOCKED"
  | "ISSUE_RESOLVED"
  | "REOPEN_ATTEMPT"
  | "REPEATED_OBSERVATION"
  | "STALE_ENTITY";
export type InsightSeverity = "INFO" | "WARNING" | "CRITICAL";

export interface EntityInsight {
  insight_id: string;
  entity_id: string;
  insight_type: InsightType;
  title: string;
  description: string;
  severity: InsightSeverity;
  observed_at: string;
  related_meeting_id: string | null;
  evidence: string;
  deterministic_sort_key: string;
}

export interface EntityInsightsResponse {
  entity_id: string;
  insight_count: number;
  insights: EntityInsight[];
}

export type AttentionLevel = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type AttentionReason =
  | "ENTITY_BLOCKED"
  | "REOPEN_ATTEMPT"
  | "ENTITY_STALE"
  | "RECENT_STATE_CHANGE"
  | "REPEATED_OBSERVATION"
  | "UNKNOWN_STATE";

export interface EntityAttention {
  attention_id: string;
  entity_id: string;
  attention_level: AttentionLevel;
  score: number;
  reasons: AttentionReason[];
  related_insight_ids: string[];
  evaluated_at: string;
}

export interface EntityAttentionResponse {
  entity_id: string;
  has_attention: boolean;
  attention: EntityAttention | null;
}

export type ActionType =
  | "ESCALATE"
  | "REQUEST_UPDATE"
  | "INVESTIGATE"
  | "FOLLOW_UP"
  | "REVIEW"
  | "NO_ACTION";
export type ActionPriority = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";

export interface EntityAction {
  action_id: string;
  entity_id: string;
  action_type: ActionType;
  priority: ActionPriority;
  recommended_action: string;
  reason: string;
  related_insight_ids: string[];
  related_meeting_id: string | null;
  created_from_observation_at: string;
  deterministic_sort_key: string;
}

export interface EntityActionsResponse {
  entity_id: string;
  action_count: number;
  actions: EntityAction[];
}

export interface DependencyEdge {
  source_entity_id: string;
  target_entity_id: string;
  relationship_type: EntityRelationshipType;
  strength: number;
  related_meeting_ids: string[];
  source_text: string | null;
  mention_id: string | null;
}

export interface DependencyPath {
  path_id: string;
  start_entity_id: string;
  end_entity_id: string;
  depth: number;
  entity_path: string[];
  relationship_path: EntityRelationshipType[];
  edges: DependencyEdge[];
  is_direct: boolean;
  is_transitive: boolean;
}

export interface DependencyGraphResponse {
  root_entity_id: string;
  direct_dependencies: DependencyPath[];
  transitive_dependencies: DependencyPath[];
  all_reachable_entity_ids: string[];
  max_depth_reached: number;
  contains_cycle: boolean;
  cycle_entity_ids: string[];
}

export type ImpactLevel = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";

export interface EntityImpact {
  impact_id: string;
  source_entity_id: string;
  impacted_entity_id: string;
  impact_level: ImpactLevel;
  risk_signals: string[];
  relationship_strength: number;
  related_meeting_ids: string[];
  reason: string;
  generated_from_at: string;
  deterministic_sort_key: string;
}

export interface EntityImpactResponse {
  entity_id: string;
  impact_count: number;
  impacts: EntityImpact[];
}
