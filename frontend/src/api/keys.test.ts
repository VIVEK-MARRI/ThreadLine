import { describe, expect, it } from "vitest";
import { queryKeys } from "./keys";

describe("tenant-aware query keys", () => {
  it("namespaces every resource key by organisation", () => {
    expect(queryKeys.meetings("org-a")).toEqual(["tl", "org-a", "meetings", null]);
    expect(queryKeys.meeting("org-a", "m-1")).toEqual(["tl", "org-a", "meeting", "m-1"]);
    expect(queryKeys.entities("org-a", "ISSUE")).toEqual(["tl", "org-a", "entities", "ISSUE"]);
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
    expect(JSON.stringify(queryKeys.queryEvidence("org-a", "q"))).not.toBe(
      JSON.stringify(queryKeys.queryEvidence("org-b", "q")),
    );
  });

  it("refuses to build scoped keys without an organisation", () => {
    expect(() => queryKeys.meetings("")).toThrow();
    expect(() => queryKeys.meetings(null as unknown as string)).toThrow();
  });
});
