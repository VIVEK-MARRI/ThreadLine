/* Meeting contracts. Shapes verified against backend OpenAPI. */

export interface MeetingResponse {
  meeting_id: string;
  title: string;
  transcript: string;
  meeting_date: string;
  participants: string[];
  ingested_at: string;
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
