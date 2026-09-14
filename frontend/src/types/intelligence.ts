/* Intelligence contracts (attention / portfolio / changes / graphs).
 * Shapes verified against backend OpenAPI. Only the fields the foundation
 * needs are modelled; feature stages extend these.
 */

export type AttentionLevel = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type RiskLevel = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type ChangeSeverity = "CRITICAL" | "HIGH" | "MEDIUM" | "INFO";

export interface AttentionItem {
  attention_id: string;
  entity_id: string;
  attention_level: AttentionLevel;
  score: number;
  reasons: string[];
  related_insight_ids: string[];
  evaluated_at: string;
}

export interface AttentionResponse {
  entity_count: number;
  items: AttentionItem[];
}

export interface PortfolioEntitySummary {
  entity_id: string;
  entity_type: string;
  canonical_name: string;
  risk_level: RiskLevel;
  attention_level: AttentionLevel | null;
  attention_score: number;
  impact_count: number;
  action_count: number;
  active_insight_count: number;
  current_state: string;
  observation_count: number;
}

export interface PortfolioResponse {
  total_entities: number;
  critical_entities: number;
  high_risk_entities: number;
  medium_risk_entities: number;
  low_risk_entities: number;
  entities_with_active_actions: number;
  entities_with_impact: number;
  blocked_entities: number;
  entities: PortfolioEntitySummary[];
  evaluated_at: string;
}

export interface OrganisationChange {
  change_id: string;
  entity_id: string;
  change_type: string;
  severity: ChangeSeverity;
  detected_at: string | null;
  meeting_id: string | null;
  mention_id: string | null;
  source_text: string | null;
  previous_state: string | null;
  current_state: string | null;
  evidence: string;
}

export interface ChangesResponse {
  total_changes: number;
  critical_changes: number;
  high_changes: number;
  medium_changes: number;
  info_changes: number;
  changes: OrganisationChange[];
  evaluated_at: string;
}

export interface DependencyGraphResponse {
  root_entity_id: string;
  direct_dependencies: unknown[];
  transitive_dependencies: unknown[];
  all_reachable_entity_ids: string[];
  max_depth_reached: number;
  contains_cycle: boolean;
  cycle_entity_ids: string[];
}
