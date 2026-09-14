import { describe, expect, it } from "vitest";
import {
  attentionReasonLabel,
  changeTypeLabel,
  displayNameFromEmail,
  formatDateTime,
  formatDuration,
  greetingFor,
  lifecycleStateLabel,
  relativeTime,
  shortId,
  snapshotLine,
} from "./dashboardFormat";

describe("dashboard formatting", () => {
  it("greets by time of day", () => {
    expect(greetingFor(new Date(2026, 0, 1, 8))).toBe("Good morning");
    expect(greetingFor(new Date(2026, 0, 1, 13))).toBe("Good afternoon");
    expect(greetingFor(new Date(2026, 0, 1, 20))).toBe("Good evening");
  });

  it("derives a display name from the user's own email", () => {
    expect(displayNameFromEmail("sam@example.com")).toBe("Sam");
    expect(displayNameFromEmail("priya.nair@example.com")).toBe("Priya");
    expect(displayNameFromEmail(null)).toBeNull();
    expect(displayNameFromEmail("@example.com")).toBeNull();
  });

  it("renders relative time only for real timestamps", () => {
    const now = Date.parse("2026-09-13T12:00:00Z");
    expect(relativeTime("2026-09-13T11:59:30Z", now)).toBe("just now");
    expect(relativeTime("2026-09-13T11:55:00Z", now)).toBe("5m ago");
    expect(relativeTime("2026-09-13T09:00:00Z", now)).toBe("3h ago");
    expect(relativeTime("2026-09-10T12:00:00Z", now)).toBe("3d ago");
    expect(relativeTime(null, now)).toBeNull();
    expect(relativeTime("not-a-date", now)).toBeNull();
  });

  it("formats durations and datetimes defensively", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(300)).toBe("5m");
    expect(formatDuration(3900)).toBe("1h 5m");
    expect(formatDuration(null)).toBeNull();
    expect(formatDateTime(null)).toBeNull();
    expect(formatDateTime("nope")).toBeNull();
    expect(formatDateTime("2026-09-10T10:00:00Z")).toMatch(/Sep/);
  });

  it("maps backend vocabularies to human copy with safe fallbacks", () => {
    expect(attentionReasonLabel("ENTITY_BLOCKED")).toBe("Blocked");
    expect(attentionReasonLabel("SOMETHING_NEW")).toBe("something new");
    expect(changeTypeLabel("RISK_ESCALATED")).toBe("Risk escalated");
    expect(changeTypeLabel("FUTURE_TYPE")).toBe("future type");
    expect(lifecycleStateLabel("IN_PROGRESS")).toBe("In progress");
    expect(lifecycleStateLabel(null)).toBe("Unknown");
  });

  it("builds the snapshot line from real counts", () => {
    expect(snapshotLine({ totalEntities: 1, attentionCount: 0, blockedCount: 0 })).toBe("1 entity");
    expect(snapshotLine({ totalEntities: 3, attentionCount: 2, blockedCount: 1 })).toBe(
      "3 entities · 2 need attention · 1 blocked",
    );
  });

  it("shortens raw ids only as a last-resort label", () => {
    expect(shortId("abc")).toBe("abc");
    expect(shortId("0123456789abcdef")).toBe("01234567…");
  });
});
