/* Meeting contracts. Shapes verified against backend OpenAPI. */

export interface MeetingResponse {
  meeting_id: string;
  title: string;
  transcript: string;
  meeting_date: string;
  participants: string[];
  ingested_at: string;
}

export type MeetingProcessingStatus = "CURRENT" | "PENDING" | "INCOMPLETE" | "FAILED" | "STALE";

export interface MeetingSummary {
  meeting_id: string;
  title: string;
  meeting_date: string;
  participants: string[];
  ingested_at: string;
  source_revision: number;
  processing_status: MeetingProcessingStatus;
  extraction_revision: number | null;
  extracted_at: string | null;
  issue_count: number;
  task_count: number;
  decision_count: number;
  risk_count: number;
  mention_count: number;
  resolved_entity_count: number;
}

export interface MeetingListResponse {
  meetings: MeetingSummary[];
  limit: number;
  returned_count: number;
  has_more: boolean;
}

export interface MeetingExtractionResponse {
  meeting_id: string;
  has_extraction: boolean;
  extraction: ExtractionResponse | null;
}

export interface MeetingProcessingResponse {
  meeting_id: string;
  source_revision: number;
  status: MeetingProcessingStatus;
  processing_complete: boolean;
  is_current: boolean;
  extraction_revision: number | null;
  derived_revision: number | null;
  semantic_revision: number | null;
  stale_mentions: number;
  worker_enabled: boolean;
}

export type MeetingMentionEntityType = "PERSON" | "ISSUE";
export type MeetingMentionResolution = "RESOLVED" | "UNRESOLVED" | "AMBIGUOUS";

export interface MeetingMention {
  mention_id: string;
  meeting_id: string;
  entity_type: MeetingMentionEntityType;
  text: string;
  source_text: string;
  entity_id: string | null;
  resolution_status: MeetingMentionResolution;
  source_revision: number;
}

export interface MeetingMentionsResponse {
  meeting_id: string;
  mention_count: number;
  resolved_mention_count: number;
  mentions: MeetingMention[];
}

export interface MeetingIngestRequest {
  title: string;
  transcript: string;
  meeting_date: string;
  participants?: string[] | null;
  meeting_id?: string | null;
}

export interface MeetingIngestResponse {
  meeting_id: string;
  status: string;
}

export interface ExtractionResponse {
  meeting_id: string;
  extracted_at: string;
  issues: Array<{
    description: string;
    evidence: { source_text: string | null };
  }>;
  tasks: Array<{
    description: string;
    owner: string | null;
    deadline: string | null;
    evidence: { source_text: string | null };
  }>;
  decisions: Array<{
    description: string;
    evidence: { source_text: string | null };
  }>;
  risks: Array<{
    description: string;
    severity: string;
    evidence: { source_text: string | null };
  }>;
}
