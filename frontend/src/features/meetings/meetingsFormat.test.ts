import { describe, expect, it } from "vitest";
import type { MeetingSummary } from "../../types/meetings";
import {
  applyLoadedMeetingFilters,
  extractedFactSummary,
  extractionRiskTone,
  formatMeetingDateTime,
  hasActiveLoadedMeetingFilters,
  isWithinMeetingDayRange,
  linkedEntitySummary,
  listFactSummary,
  meetingSearchText,
  parseMeetingListLimit,
  parseMeetingSort,
  parseMeetingStatusFilter,
  participantPreview,
  processingStatusLabel,
  processingStatusTone,
  relativeMeetingTime,
  riskSeverityLabel,
  transcriptSizeLabel,
  uniqueResolvedEntityIds,
} from "./meetingsFormat";

function summary(overrides: Partial<MeetingSummary> = {}): MeetingSummary {
  return {
    meeting_id: "m-1",
    title: "Product Review",
    meeting_date: "2026-09-10T10:00:00Z",
    participants: ["Alice", "Bob"],
    ingested_at: "2026-09-10T11:00:00Z",
    source_revision: 1,
    processing_status: "CURRENT",
    extraction_revision: 1,
    extracted_at: "2026-09-10T11:05:00Z",
    issue_count: 1,
    task_count: 1,
    decision_count: 1,
    risk_count: 1,
    mention_count: 2,
    resolved_entity_count: 1,
    ...overrides,
  };
}

describe("meeting formatting", () => {
  it("parses only supported list controls", () => {
    expect(parseMeetingListLimit("100")).toBe(100);
    expect(parseMeetingListLimit("1000")).toBe(50);
    expect(parseMeetingStatusFilter("FAILED")).toBe("FAILED");
    expect(parseMeetingStatusFilter("COMPLETE")).toBe("ALL");
    expect(parseMeetingSort("oldest")).toBe("oldest");
    expect(parseMeetingSort("random")).toBe("newest");
  });

  it("formats real timestamps defensively", () => {
    expect(formatMeetingDateTime("2026-09-10T10:00:00Z")).toMatch(/Sep/);
    expect(formatMeetingDateTime("invalid")).toBeNull();
    expect(relativeMeetingTime(null)).toBeNull();
    expect(relativeMeetingTime("2026-09-13T11:59:30Z", Date.parse("2026-09-13T12:00:00Z"))).toBe(
      "just now",
    );
  });

  it("maps backend processing states without inventing stages", () => {
    expect(processingStatusLabel("STALE")).toBe("Needs refresh");
    expect(processingStatusTone("FAILED")).toBe("danger");
    expect(riskSeverityLabel("  ")).toBe("Severity not stated");
    expect(extractionRiskTone("Unstated severity")).toBe("neutral");
  });

  it("summarizes participants, facts, and entities honestly", () => {
    expect(participantPreview(["A", "B", "C", "D"])).toEqual({
      shown: ["A", "B", "C"],
      remaining: 1,
    });
    expect(listFactSummary(summary({ extracted_at: null }))).toBe("Not extracted yet");
    expect(listFactSummary(summary())).toBe("1 decision · 1 task · 1 issue · 1 risk");
    expect(
      extractedFactSummary({
        meeting_id: "m-1",
        extracted_at: "2026-09-10T11:05:00Z",
        issues: [],
        tasks: [],
        decisions: [],
        risks: [],
      }),
    ).toBe("Extracted · no facts");
    expect(linkedEntitySummary(0)).toBe("No linked entities");
    expect(transcriptSizeLabel("abc")).toMatch(/3 characters/);
  });

  it("filters and sorts only the loaded meeting set", () => {
    const meetings = [
      summary({ meeting_id: "old", title: "Old sync", meeting_date: "2026-09-01T10:00:00Z" }),
      summary({
        meeting_id: "new",
        title: "New review",
        meeting_date: "2026-09-10T10:00:00Z",
        processing_status: "FAILED",
      }),
    ];
    expect(
      applyLoadedMeetingFilters(meetings, {
        query: "review",
        status: "ALL",
        fromDate: "",
        toDate: "",
        sort: "newest",
      }).map((meeting) => meeting.meeting_id),
    ).toEqual(["new"]);
    expect(
      applyLoadedMeetingFilters(meetings, {
        query: "",
        status: "FAILED",
        fromDate: "2026-09-09",
        toDate: "2026-09-11",
        sort: "oldest",
      }).map((meeting) => meeting.meeting_id),
    ).toEqual(["new"]);
    expect(isWithinMeetingDayRange("invalid", "", "")).toBe(false);
    expect(meetingSearchText(meetings[0])).toContain("old sync");
    expect(
      hasActiveLoadedMeetingFilters({
        query: "",
        status: "ALL",
        fromDate: "",
        toDate: "",
        sort: "newest",
      }),
    ).toBe(false);
  });

  it("derives resolved entity ids deterministically", () => {
    expect(
      uniqueResolvedEntityIds([
        {
          mention_id: "b",
          meeting_id: "m-1",
          entity_type: "ISSUE",
          text: "x",
          source_text: "x",
          entity_id: "ent-b",
          resolution_status: "RESOLVED",
          source_revision: 1,
        },
        {
          mention_id: "a",
          meeting_id: "m-1",
          entity_type: "ISSUE",
          text: "y",
          source_text: "y",
          entity_id: "ent-a",
          resolution_status: "RESOLVED",
          source_revision: 1,
        },
        {
          mention_id: "c",
          meeting_id: "m-1",
          entity_type: "ISSUE",
          text: "z",
          source_text: "z",
          entity_id: null,
          resolution_status: "UNRESOLVED",
          source_revision: 1,
        },
      ]),
    ).toEqual(["ent-a", "ent-b"]);
  });
});
