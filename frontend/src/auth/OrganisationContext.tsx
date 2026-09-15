/* Organisation context: current org, role, and switching.
 *
 * Membership is the authority: the current organisation must always be one
 * of the authenticated user's ACTIVE memberships. A stored selection that
 * no longer matches (removed, disabled org) falls back to the first
 * available membership — never to an arbitrary id.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { OrganisationMembershipView, Role } from "../types/auth";
import { readOrganisationId, writeOrganisationId } from "./session";
import { readOrganisationRef, useAuth } from "./AuthContext";

export interface OrganisationContextValue {
  organisations: OrganisationMembershipView[];
  current: OrganisationMembershipView | null;
  organisationId: string | null;
  role: Role | null;
  /** True once the stored/auto selection has been resolved for the current user. */
  selectionResolved: boolean;
  /** Switch to one of the user's own organisations. Returns false if unknown. */
  select: (organisationId: string) => boolean;
}

const OrganisationContext = createContext<OrganisationContextValue | null>(null);

export function OrganisationProvider({ children }: { children: ReactNode }): React.JSX.Element {
  const { user, memberships } = useAuth();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectionResolved, setSelectionResolved] = useState(false);
  // Mirror the live selection so the API client's getter always reads the
  // current organisation — including between render and passive effects,
  // where a cache-cleared refetch could otherwise fire without a tenant.
  const selectedIdRef = useRef<string | null>(null);
  selectedIdRef.current = selectedId;

  // Keep the selection valid against live memberships.
  useEffect(() => {
    if (!user) {
      setSelectedId(null);
      setSelectionResolved(false);
      return;
    }
    const active = memberships.filter((m) => m.status === "ACTIVE");
    const stored = readOrganisationId(user.user_id);
    if (stored && active.some((m) => m.organisation.organisation_id === stored)) {
      setSelectedId((current) => (current === stored ? current : stored));
    } else {
      setSelectedId(active[0]?.organisation.organisation_id ?? null);
    }
    // Mark resolved in the same commit: guards must never redirect on the
    // transient (memberships present, selection not yet applied) render —
    // that redirect unmounts the page, drops local state, and refetches.
    setSelectionResolved(true);
  }, [user, memberships]);

  // Persist a valid selection for this user only.
  useEffect(() => {
    if (user && selectedId) writeOrganisationId(user.user_id, selectedId);
  }, [user, selectedId]);

  // Feed the API client with the live selection (hint header only). The
  // getter is installed once and reads the mirror ref, so organisation
  // transitions never expose a window where requests lose their scope.
  useEffect(() => {
    readOrganisationRef.current = () => selectedIdRef.current;
    return () => {
      readOrganisationRef.current = null;
    };
  }, []);

  const select = useCallback(
    (organisationId: string): boolean => {
      const known = memberships.some(
        (m) => m.status === "ACTIVE" && m.organisation.organisation_id === organisationId,
      );
      if (!known) return false;
      // Scope the API client to the new organisation synchronously: clearing
      // the query cache retriggers refetches before React commits this state
      // change, and those requests must already carry the new tenant header.
      selectedIdRef.current = organisationId;
      // Organisation switch = new tenant namespace: drop cached server data.
      // Query keys already segment by organisation; clearing removes any
      // lingering per-org entries from the previous selection promptly.
      queryClient.clear();
      setSelectedId(organisationId);
      return true;
    },
    [memberships, queryClient],
  );

  const current = useMemo(
    () =>
      memberships.find((m) => m.organisation.organisation_id === selectedId) ?? null,
    [memberships, selectedId],
  );

  const value = useMemo<OrganisationContextValue>(
    () => ({
      organisations: memberships,
      current,
      organisationId: current?.organisation.organisation_id ?? null,
      role: current?.role ?? null,
      selectionResolved,
      select,
    }),
    [memberships, current, selectionResolved, select],
  );

  return <OrganisationContext.Provider value={value}>{children}</OrganisationContext.Provider>;
}

export function useOrganisation(): OrganisationContextValue {
  const context = useContext(OrganisationContext);
  if (!context) throw new Error("useOrganisation must be used inside <OrganisationProvider>.");
  return context;
}
