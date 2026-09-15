import { describe, expect, it } from "vitest";
import { queryKeys } from "./keys";

describe("tenant-aware query keys", () => {
  it("namespaces every resource key by organisation", () => {
    expect(queryKeys.meetings("org-a")).toEqual(["tl", "org-a", "meetings", null]);
    expect(queryKeys.meetings("org-a", { limit: 50 })).toEqual([
      "tl",
      "org-a",
      "meetings",
      { limit: 50 },
    ]);
    expect(queryKeys.meeting("org-a", "m-1")).toEqual(["tl", "org-a", "meeting", "m-1"]);
    expect(queryKeys.meetingSection("org-a", "m-1", "processing")).toEqual([
      "tl",
      "org-a",
      "meeting",
      "m-1",
      "processing",
    ]);
    expect(queryKeys.entities("org-a", "ISSUE")).toEqual(["tl", "org-a", "entities", "ISSUE"]);
    expect(queryKeys.attention("org-a")).toEqual(["tl", "org-a", "attention"]);
    expect(queryKeys.portfolio("org-a")).toEqual(["tl", "org-a", "portfolio"]);
    expect(queryKeys.changes("org-a", { limit: 8 })).toEqual([
      "tl",
      "org-a",
      "changes",
      { limit: 8 },
    ]);
    expect(queryKeys.jobs("org-a")).toEqual(["tl", "org-a", "jobs"]);
    expect(queryKeys.queryEvidence("org-a", "status?")).toEqual([
      "tl",
      "org-a",
      "query",
      "evidence",
      "status?",
      null,
    ]);
  });

  it("never reuses Tenant A entries for Tenant B", () => {
    const a = JSON.stringify(queryKeys.meetings("org-a"));
    const b = JSON.stringify(queryKeys.meetings("org-b"));
    expect(a).not.toBe(b);
    expect(JSON.stringify(queryKeys.entity("org-a", "e-1"))).not.toBe(
      JSON.stringify(queryKeys.entity("org-b", "e-1")),
    );
    expect(JSON.stringify(queryKeys.meeting("org-a", "m-1"))).not.toBe(
      JSON.stringify(queryKeys.meeting("org-b", "m-1")),
    );
    expect(JSON.stringify(queryKeys.meetingSection("org-a", "m-1", "processing"))).not.toBe(
      JSON.stringify(queryKeys.meetingSection("org-b", "m-1", "processing")),
    );
    expect(JSON.stringify(queryKeys.queryEvidence("org-a", "q"))).not.toBe(
      JSON.stringify(queryKeys.queryEvidence("org-b", "q")),
    );
    expect(JSON.stringify(queryKeys.attention("org-a"))).not.toBe(
      JSON.stringify(queryKeys.attention("org-b")),
    );
    expect(JSON.stringify(queryKeys.portfolio("org-a"))).not.toBe(
      JSON.stringify(queryKeys.portfolio("org-b")),
    );
    expect(JSON.stringify(queryKeys.changes("org-a", { limit: 8 }))).not.toBe(
      JSON.stringify(queryKeys.changes("org-b", { limit: 8 })),
    );
    expect(JSON.stringify(queryKeys.jobs("org-a"))).not.toBe(
      JSON.stringify(queryKeys.jobs("org-b")),
    );
  });

  it("refuses to build scoped keys without an organisation", () => {
    expect(() => queryKeys.meetings("")).toThrow();
    expect(() => queryKeys.meetings(null as unknown as string)).toThrow();
  });
});
