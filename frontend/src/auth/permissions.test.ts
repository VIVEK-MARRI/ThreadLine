import { describe, expect, it } from "vitest";
import {
  canCreateMeeting,
  canGrantOwner,
  canManageMembers,
  canManageOrganisation,
  canManageRoles,
  canReadMeeting,
  canRunMeetingProcessing,
  canUpdateMeeting,
  canViewDiagnostics,
  roleAtLeast,
} from "./permissions";

describe("frontend role hints", () => {
  it("ranks OWNER above ADMIN above MEMBER", () => {
    expect(roleAtLeast("MEMBER", "MEMBER")).toBe(true);
    expect(roleAtLeast("MEMBER", "ADMIN")).toBe(false);
    expect(roleAtLeast("ADMIN", "ADMIN")).toBe(true);
    expect(roleAtLeast("ADMIN", "OWNER")).toBe(false);
    expect(roleAtLeast("OWNER", "OWNER")).toBe(true);
    expect(roleAtLeast(null, "MEMBER")).toBe(false);
  });

  it("gates admin affordances to ADMIN and above", () => {
    expect(canManageMembers("MEMBER")).toBe(false);
    expect(canManageMembers("ADMIN")).toBe(true);
    expect(canManageRoles("ADMIN")).toBe(true);
    expect(canViewDiagnostics("MEMBER")).toBe(false);
    expect(canViewDiagnostics("ADMIN")).toBe(true);
  });

  it("reserves ownership powers for OWNER", () => {
    expect(canGrantOwner("ADMIN")).toBe(false);
    expect(canGrantOwner("OWNER")).toBe(true);
    expect(canManageOrganisation("ADMIN")).toBe(false);
    expect(canManageOrganisation("OWNER")).toBe(true);
  });

  it("matches backend meeting permissions for every authenticated role", () => {
    for (const role of ["MEMBER", "ADMIN", "OWNER"] as const) {
      expect(canCreateMeeting(role)).toBe(true);
      expect(canReadMeeting(role)).toBe(true);
      expect(canUpdateMeeting(role)).toBe(true);
      expect(canRunMeetingProcessing(role)).toBe(true);
    }
    expect(canCreateMeeting(null)).toBe(false);
    expect(canReadMeeting(null)).toBe(false);
    expect(canUpdateMeeting(null)).toBe(false);
    expect(canRunMeetingProcessing(null)).toBe(false);
  });
});
