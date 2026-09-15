/* Tenant-aware TanStack Query key factory.
 *
 * INVARIANT: every server-data cache key begins with the organisation scope:
 *
 *   ["tl", organisationId, resource, identifier?, filters?]
 *
 * A cache entry from Tenant A can never be reused for Tenant B, because the
 * organisation segment differs and queries are only enabled when an
 * organisation is selected. Switching organisations therefore switches the
 * entire cache namespace; signing out clears it outright (see providers).
 */

export type QueryKey = readonly [string, string, ...unknown[]];

function scope(organisationId: string | null | undefined): string {
  if (!organisationId) {
    throw new Error("Query cache scope requires an organisation id.");
  }
  return organisationId;
}

export const queryKeys = {
  me: () => ["tl", "session", "me"] as const,
  myOrganisations: (userId: string) => ["tl", "session", "orgs", userId] as const,
  organisation: (organisationId: string) =>
    ["tl", scope(organisationId), "organisation"] as const,
  members: (organisationId: string) =>
    ["tl", scope(organisationId), "members"] as const,
  meetings: (organisationId: string, filters?: Record<string, unknown>) =>
    ["tl", scope(organisationId), "meetings", filters ?? null] as const,
  meeting: (organisationId: string, meetingId: string) =>
    ["tl", scope(organisationId), "meeting", meetingId] as const,
  meetingSection: (organisationId: string, meetingId: string, section: string) =>
    ["tl", scope(organisationId), "meeting", meetingId, section] as const,
  entities: (organisationId: string, entityType?: string | null) =>
    ["tl", scope(organisationId), "entities", entityType ?? null] as const,
  entity: (organisationId: string, entityId: string) =>
    ["tl", scope(organisationId), "entity", entityId] as const,
  entitySection: (organisationId: string, entityId: string, section: string) =>
    ["tl", scope(organisationId), "entity", entityId, section] as const,
  attention: (organisationId: string) =>
    ["tl", scope(organisationId), "attention"] as const,
  portfolio: (organisationId: string) =>
    ["tl", scope(organisationId), "portfolio"] as const,
  changes: (organisationId: string, filters?: Record<string, unknown>) =>
    ["tl", scope(organisationId), "changes", filters ?? null] as const,
  queryEvidence: (organisationId: string, question: string, entityId?: string | null) =>
    ["tl", scope(organisationId), "query", "evidence", question, entityId ?? null] as const,
  jobs: (organisationId: string) => ["tl", scope(organisationId), "jobs"] as const,
  diagnostics: (organisationId: string) =>
    ["tl", scope(organisationId), "diagnostics"] as const,
};
