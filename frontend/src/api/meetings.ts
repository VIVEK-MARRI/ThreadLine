/* Meeting feature API. */

import { request } from "./client";
import type {
  ExtractionResponse,
  MeetingExtractionResponse,
  MeetingIngestRequest,
  MeetingIngestResponse,
  MeetingListResponse,
  MeetingMentionsResponse,
  MeetingProcessingResponse,
  MeetingResponse,
} from "../types/meetings";

export const meetingsApi = {
  list(limit = 50): Promise<MeetingListResponse> {
    return request<MeetingListResponse>("/api/v1/meetings", { query: { limit } });
  },

  ingest(payload: MeetingIngestRequest): Promise<MeetingIngestResponse> {
    return request<MeetingIngestResponse>("/api/v1/meetings", {
      method: "POST",
      body: payload,
    });
  },

  get(meetingId: string): Promise<MeetingResponse> {
    return request<MeetingResponse>(`/api/v1/meetings/${encodeURIComponent(meetingId)}`);
  },

  getExtraction(meetingId: string): Promise<MeetingExtractionResponse> {
    return request<MeetingExtractionResponse>(
      `/api/v1/meetings/${encodeURIComponent(meetingId)}/extraction`,
    );
  },

  getProcessing(meetingId: string): Promise<MeetingProcessingResponse> {
    return request<MeetingProcessingResponse>(
      `/api/v1/meetings/${encodeURIComponent(meetingId)}/processing`,
    );
  },

  listMentions(meetingId: string): Promise<MeetingMentionsResponse> {
    return request<MeetingMentionsResponse>(
      `/api/v1/meetings/${encodeURIComponent(meetingId)}/mentions`,
    );
  },

  revise(meetingId: string, payload: MeetingIngestRequest): Promise<MeetingIngestResponse> {
    return request<MeetingIngestResponse>(
      `/api/v1/meetings/${encodeURIComponent(meetingId)}`,
      { method: "PUT", body: payload },
    );
  },

  extract(meetingId: string): Promise<ExtractionResponse> {
    return request<ExtractionResponse>(
      `/api/v1/meetings/${encodeURIComponent(meetingId)}/extract`,
      { method: "POST" },
    );
  },
};
