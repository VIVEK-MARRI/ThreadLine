/* Dashboard data layer.
 *
 * One hook per dashboard question, each backed by a real backend endpoint
 * and a tenant-scoped query key (["tl", organisationId, ...]). Queries run
 * in parallel — no waterfalls — and fail independently so one backend error
 * degrades a single section instead of the whole page.
 *
 * No intelligence logic lives here: ordering, levels, scores, and counts
 * all come from the API. Hooks only scope, bound, and join for display
 * (attention items join entity names from the portfolio directory).
 */

import { useMemo } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { intelligenceApi, jobsApi } from "../../api/intelligence";
import { meetingsApi } from "../../api/meetings";
import { queryKeys } from "../../api/keys";
import { useOrganisation } from "../../auth/OrganisationContext";
import type {
  AttentionResponse,
  ChangesResponse,
  PortfolioEntitySummary,
  PortfolioResponse,
} from "../../types/intelligence";
import type { JobHealth } from "../../types/jobs";

export const DASHBOARD_ATTENTION_LIMIT = 5;
export const DASHBOARD_CHANGES_LIMIT = 8;
export const DASHBOARD_RISKS_LIMIT = 5;
export const DASHBOARD_MEETINGS_LIMIT = 5;

export function useDashboardAttention() {
  const { organisationId } = useOrganisation();
  return useQuery<AttentionResponse>({
    queryKey: queryKeys.attention(organisationId ?? ""),
    queryFn: () => intelligenceApi.attention(),
    enabled: Boolean(organisationId),
    staleTime: 30_000,
  });
}

export function useDashboardPortfolio() {
  const { organisationId } = useOrganisation();
  return useQuery<PortfolioResponse>({
    queryKey: queryKeys.portfolio(organisationId ?? ""),
    queryFn: () => intelligenceApi.portfolio(),
    enabled: Boolean(organisationId),
    staleTime: 30_000,
  });
}

export function useDashboardChanges() {
  const { organisationId } = useOrganisation();
  const filters = useMemo(
    () => ({ limit: DASHBOARD_CHANGES_LIMIT }),
    [],
  );
  return useQuery<ChangesResponse>({
    queryKey: queryKeys.changes(organisationId ?? "", filters),
    queryFn: () => intelligenceApi.changes({ limit: DASHBOARD_CHANGES_LIMIT }),
    enabled: Boolean(organisationId),
    staleTime: 30_000,
  });
}

export function useDashboardJobHealth() {
  const { organisationId } = useOrganisation();
  return useQuery<JobHealth>({
    queryKey: queryKeys.jobs(organisationId ?? ""),
    queryFn: () => jobsApi.health(),
    enabled: Boolean(organisationId),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

export interface EntityDirectoryEntry extends PortfolioEntitySummary {
  displayName: string;
}

/**
 * Entity directory from the portfolio: entity_id → name, type, state,
 * risk. Attention and change rows join through this instead of fetching
 * per-entity detail (no N+1).
 */
export function useEntityDirectory(): Map<string, EntityDirectoryEntry> {
  const portfolio = useDashboardPortfolio();
  return useMemo(() => {
    const directory = new Map<string, EntityDirectoryEntry>();
    for (const entity of portfolio.data?.entities ?? []) {
      directory.set(entity.entity_id, { ...entity, displayName: entity.canonical_name });
    }
    return directory;
  }, [portfolio.data]);
}

/**
 * Resolve titles for meeting ids referenced by recent changes.
 * Bounded fan-out (≤ DASHBOARD_MEETINGS_LIMIT unique ids), fetched in
 * parallel under the standard per-meeting tenant keys so results are
 * shared with the meeting detail cache. A failed title degrades to a
 * plain "open meeting" link — never a section error.
 */
export function useDashboardMeetingTitles(meetingIds: string[]): Map<string, string> {
  const { organisationId } = useOrganisation();
  const unique = useMemo(
    () => Array.from(new Set(meetingIds.filter(Boolean))).slice(0, DASHBOARD_MEETINGS_LIMIT),
    [meetingIds],
  );
  const results = useQueries({
    queries: unique.map((meetingId) => ({
      queryKey: queryKeys.meeting(organisationId ?? "", meetingId),
      queryFn: () => meetingsApi.get(meetingId),
      enabled: Boolean(organisationId && meetingId),
      staleTime: 60_000,
      retry: 1,
    })),
  });
  return useMemo(() => {
    const titles = new Map<string, string>();
    results.forEach((result, index) => {
      const title = result.data?.title?.trim();
      if (title) titles.set(unique[index], title);
    });
    return titles;
  }, [results, unique]);
}
