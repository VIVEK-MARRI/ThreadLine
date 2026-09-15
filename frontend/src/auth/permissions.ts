/* Frontend role mirror — rendering hints ONLY.
 *
 * The backend enforces every permission server-side. These helpers exist so
 * the UI can hide affordances the caller cannot use (less clutter, fewer
 * dead ends). They must never gate data fetching or be treated as security.
 */

import type { Role } from "../types/auth";

const RANK: Record<Role, number> = { MEMBER: 1, ADMIN: 2, OWNER: 3 };

export function roleAtLeast(role: Role | null, minimum: Role): boolean {
  if (!role) return false;
  return RANK[role] >= RANK[minimum];
}

export function canManageMembers(role: Role | null): boolean {
  return roleAtLeast(role, "ADMIN");
}

export function canManageRoles(role: Role | null): boolean {
  return roleAtLeast(role, "ADMIN");
}

export function canGrantOwner(role: Role | null): boolean {
  return role === "OWNER";
}

export function canManageOrganisation(role: Role | null): boolean {
  return role === "OWNER";
}

export function canViewDiagnostics(role: Role | null): boolean {
  return roleAtLeast(role, "ADMIN");
}

/* Meeting-workspace affordances mirror backend ROLE_PERMISSIONS today:
 * MEMBER, ADMIN, and OWNER all hold MEETING_CREATE/READ/UPDATE and
 * PROCESSING_RUN. These helpers keep that assumption in one tested place;
 * the backend remains authoritative if the policy ever changes.
 */
export function canCreateMeeting(role: Role | null): boolean {
  return role !== null;
}

export function canReadMeeting(role: Role | null): boolean {
  return role !== null;
}

export function canUpdateMeeting(role: Role | null): boolean {
  return role !== null;
}

export function canRunMeetingProcessing(role: Role | null): boolean {
  return role !== null;
}
