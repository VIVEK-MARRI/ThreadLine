/* Intelligence workspace data layer.
 *
 * Each section owns one backend question. Organisation-wide attention,
 * portfolio, and change collections load in parallel and fail independently.
 * Related names and meeting titles come from already-loaded portfolio and
 * meeting-list responses; the page never issues one lookup per signal.
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { intelligenceApi } from "../../api/intelligence";
import { meetingsApi } from "../../api/meetings";
import { queryKeys } from "../../api/keys";
import { useOrganisation } from "../../auth/OrganisationContext";
import type {
  AttentionResponse,
  ChangeSeverity,
  ChangesResponse,
  PortfolioEntitySummary,
  PortfolioResponse,
  ScanStatusResponse,
} from "../../types/intelligence";
import type { IntelligenceChangeFilters } from "./intelligenceFormat";
import {
  INTELLIGENCE_CHANGE_STREAM_LIMIT,
  INTELLIGENCE_MEETING_DIRECTORY_LIMIT,
  INTELLIGENCE_MOVEMENT_LIMIT,
  INTELLIGENCE_REPEATED_LIMIT,
  selectMeetingDirectory,
} from "./intelligenceFormat";

export type { IntelligenceChangeFilters };

export function useIntelligenceAttention() {
  const { organisationId } = useOrganisation();
  return useQuery<AttentionResponse>({
    queryKey: queryKeys.attention(organisationId ?? ""),
    queryFn: () => intelligenceApi.attention(),
    enabled: Boolean(organisationId),
    staleTime: 30_000,
  });
}

export function useIntelligencePortfolio() {
  const { organisationId } = useOrganisation();
  return useQuery<PortfolioResponse>({
    queryKey: queryKeys.portfolio(organisationId ?? ""),
    queryFn: () => intelligenceApi.portfolio(),
    enabled: Boolean(organisationId),
    staleTime: 30_000,
  });
}

export function useIntelligenceEntityDirectory(): Map<string, PortfolioEntitySummary> {
  const portfolio = useIntelligencePortfolio();
  return useMemo(() => {
    const directory = new Map<string, PortfolioEntitySummary>();
    for (const entity of portfolio.data?.entities ?? []) {
      directory.set(entity.entity_id, entity);
    }
    return directory;
  }, [portfolio.data]);
}

export function useIntelligenceChangeStream(filters: IntelligenceChangeFilters) {
  const { organisationId } = useOrganisation();
  const serverParams = useMemo(() => {
    const params: { limit: number; severity?: ChangeSeverity; change_type?: string } = {
      limit: INTELLIGENCE_CHANGE_STREAM_LIMIT,
    };
    if (filters.severity !== "ALL") params.severity = filters.severity;
    if (filters.changeType !== "ALL") params.change_type = filters.changeType;
    return params;
  }, [filters.severity, filters.changeType]);
  return useQuery<ChangesResponse>({
    queryKey: queryKeys.changes(organisationId ?? "", serverParams),
    queryFn: () => intelligenceApi.changes(serverParams),
    enabled: Boolean(organisationId),
    staleTime: 30_000,
  });
}

export function useIntelligenceRepeatedSignals() {
  const { organisationId } = useOrganisation();
  const filters = useMemo(
    () => ({ change_type: "REPEATED_UNRESOLVED", limit: INTELLIGENCE_REPEATED_LIMIT }),
    [],
  );
  return useQuery<ChangesResponse>({
    queryKey: queryKeys.changes(organisationId ?? "", filters),
    queryFn: () => intelligenceApi.changes(filters),
    enabled: Boolean(organisationId),
    staleTime: 30_000,
  });
}

export function useIntelligenceScanStatus() {
  const { organisationId } = useOrganisation();
  return useQuery<ScanStatusResponse>({
    queryKey: queryKeys.scanStatus(organisationId ?? ""),
    queryFn: () => intelligenceApi.scanStatus(),
    enabled: Boolean(organisationId),
    staleTime: 30_000,
  });
}

export function useIntelligenceMovement() {
  const { organisationId } = useOrganisation();
  const filters = useMemo(() => ({ limit: INTELLIGENCE_MOVEMENT_LIMIT }), []);
  return useQuery<ChangesResponse>({
    queryKey: queryKeys.changes(organisationId ?? "", { ...filters, view: "movement" }),
    queryFn: () => intelligenceApi.changes(filters),
    enabled: Boolean(organisationId),
    staleTime: 30_000,
  });
}

export function useIntelligenceMeetingDirectory() {
  const { organisationId } = useOrganisation();
  const query = useQuery({
    queryKey: queryKeys.meetings(organisationId ?? "", {
      limit: INTELLIGENCE_MEETING_DIRECTORY_LIMIT,
    }),
    queryFn: () => meetingsApi.list(INTELLIGENCE_MEETING_DIRECTORY_LIMIT),
    enabled: Boolean(organisationId),
    staleTime: 30_000,
  });
  const directory = useMemo(() => selectMeetingDirectory(query.data?.meetings), [query.data]);
  return { query, directory };
}
