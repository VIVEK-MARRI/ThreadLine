/* Meeting feature API. */

import { request } from "./client";
import type {
  ExtractionResponse,
  MeetingIngestRequest,
  MeetingIngestResponse,
  MeetingResponse,
} from "../types/meetings";

export const meetingsApi = {
  ingest(payload: MeetingIngestRequest): Promise<MeetingIngestResponse> {
    return request<MeetingIngestResponse>("/api/v1/meetings", {
      method: "POST",
      body: payload,
    });
  },

  get(meetingId: string): Promise<MeetingResponse> {
    return request<MeetingResponse>(`/api/v1/meetings/${encodeURIComponent(meetingId)}`);
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
