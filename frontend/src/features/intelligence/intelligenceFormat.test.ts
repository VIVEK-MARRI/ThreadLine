import { describe, expect, it } from "vitest";
import type {
  AttentionItem,
  OrganisationChange,
  PortfolioEntitySummary,
  PortfolioResponse,
} from "../../types/intelligence";
import type { MeetingSummary } from "../../types/meetings";
import {
  filterChangesByEntityType,
  hasActiveStreamFilters,
  orderChangesNewestFirst,
  parseChangeSeverity,
  parseChangeType,
  parseIntelligenceEntityType,
  selectAttentionSignals,
  selectFollowUpEntities,
  selectFreshnessTimestamp,
  selectImpactDependencyChanges,
  selectMeetingDirectory,
} from "./intelligenceFormat";

function change(overrides: Partial<OrganisationChange> = {}): OrganisationChange {
  return {
    change_id: "chg-1",
    entity_id: "ent-pay",
    change_type: "STATE_BLOCKED",
    severity: "HIGH",
    detected_at: "2026-09-12T10:00:00Z",
    meeting_id: "mtg-1",
    mention_id: null,
    source_text: null,
    previous_state: "IN_PROGRESS",
    current_state: "BLOCKED",
    insight_id: null,
    dependency_path: null,
    impact_count: null,
    related_entity_ids: [],
    evidence: "Payment migration transitioned to BLOCKED.",
    ...overrides,
  };
}

function summary(overrides: Partial<PortfolioEntitySummary> = {}): PortfolioEntitySummary {
  return {
    entity_id: "ent-pay",
    entity_type: "ISSUE",
    canonical_name: "Payment migration",
    risk_level: "HIGH",
    attention_level: "HIGH",
    attention_score: 58,
    impact_count: 1,
    action_count: 1,
    active_insight_count: 2,
    current_state: "BLOCKED",
    observation_count: 4,
    ...overrides,
  };
}

describe("intelligence stream controls", () => {
  it("accepts only real backend severity and change-type values", () => {
    expect(parseChangeSeverity("CRITICAL")).toBe("CRITICAL");
    expect(parseChangeSeverity("URGENT")).toBe("ALL");
    expect(parseChangeType("STATE_BLOCKED")).toBe("STATE_BLOCKED");
    expect(parseChangeType("BUSINESS_VALUE")).toBe("ALL");
    expect(parseIntelligenceEntityType("PERSON")).toBe("PERSON");
    expect(parseIntelligenceEntityType("PROJECT")).toBe("ALL");
  });

  it("reports active controls without inventing a search query", () => {
    expect(
      hasActiveStreamFilters({ severity: "ALL", changeType: "ALL", entityType: "ALL" }),
    ).toBe(false);
    expect(
      hasActiveStreamFilters({ severity: "HIGH", changeType: "ALL", entityType: "ALL" }),
    ).toBe(true);
  });
});

describe("intelligence selectors preserve backend data", () => {
  it("preserves backend attention order and applies the display limit", () => {
    const items: AttentionItem[] = [
      {
        attention_id: "first",
        entity_id: "ent-pay",
        attention_level: "CRITICAL",
        score: 120,
        reasons: ["ENTITY_BLOCKED"],
        related_insight_ids: ["ins-1"],
        evaluated_at: "2026-09-12T10:00:00Z",
      },
      {
        attention_id: "second",
        entity_id: "ent-api",
        attention_level: "HIGH",
        score: 60,
        reasons: ["RECENT_STATE_CHANGE"],
        related_insight_ids: [],
        evaluated_at: "2026-09-12T10:00:00Z",
      },
    ];
    expect(selectAttentionSignals(items, 1).map((item) => item.attention_id)).toEqual(["first"]);
    expect(selectAttentionSignals(items, 5).map((item) => item.attention_id)).toEqual([
      "first",
      "second",
    ]);
  });

  it("filters loaded changes by portfolio entity type without changing server semantics", () => {
    const directory = new Map([["ent-pay", summary()]]);
    const rows = [
      change(),
      change({ change_id: "chg-2", entity_id: "ent-rahul", change_type: "STATE_STARTED" }),
    ];
    expect(
      filterChangesByEntityType(rows, "ISSUE", directory).map((row) => row.change_id),
    ).toEqual(["chg-1"]);
    expect(
      filterChangesByEntityType(rows, "PERSON", directory).map((row) => row.change_id),
    ).toEqual([]);
  });

  it("selects only backend-classified dependency and impact movement", () => {
    const rows = [
      change(),
      change({ change_id: "chg-new", change_type: "NEW_DEPENDENCY" }),
      change({ change_id: "chg-expanded", change_type: "DEPENDENCY_EXPANDED" }),
      change({ change_id: "chg-impact", change_type: "IMPACT_EXPANDED" }),
    ];
    expect(selectImpactDependencyChanges(rows).map((row) => row.change_id)).toEqual([
      "chg-new",
      "chg-expanded",
      "chg-impact",
    ]);
  });

  it("orders recent movement newest-first and leaves undated records last", () => {
    const rows = [
      change({ change_id: "old", detected_at: "2026-09-10T10:00:00Z" }),
      change({ change_id: "undated", detected_at: null }),
      change({ change_id: "new", detected_at: "2026-09-12T10:00:00Z" }),
    ];
    expect(orderChangesNewestFirst(rows).map((row) => row.change_id)).toEqual([
      "new",
      "old",
      "undated",
    ]);
  });

  it("lists portfolio entities that already have recommended actions", () => {
    const entities = [
      summary({ entity_id: "ent-pay", action_count: 2 }),
      summary({ entity_id: "ent-api", canonical_name: "Provider API", action_count: 0 }),
    ];
    const portfolio: PortfolioResponse = {
      total_entities: 2,
      critical_entities: 0,
      high_risk_entities: 1,
      medium_risk_entities: 0,
      low_risk_entities: 1,
      entities_with_active_actions: 1,
      entities_with_impact: 0,
      blocked_entities: 1,
      entities,
      evaluated_at: "2026-09-12T10:00:00Z",
    };
    expect(selectFollowUpEntities(portfolio, 5).map((entity) => entity.entity_id)).toEqual([
      "ent-pay",
    ]);
  });

  it("deduplicates meeting titles and selects the latest evaluated timestamp", () => {
    const meetings: MeetingSummary[] = [
      {
        meeting_id: "mtg-1",
        title: "Payment sync",
        meeting_date: "2026-09-12T10:00:00Z",
        participants: [],
        ingested_at: "2026-09-12T10:05:00Z",
        source_revision: 1,
        processing_status: "CURRENT",
        extraction_revision: 1,
        extracted_at: "2026-09-12T10:06:00Z",
        issue_count: 0,
        task_count: 0,
        decision_count: 0,
        risk_count: 0,
        mention_count: 0,
        resolved_entity_count: 0,
      },
      {
        meeting_id: "mtg-1",
        title: "Duplicate sync",
        meeting_date: "2026-09-12T10:00:00Z",
        participants: [],
        ingested_at: "2026-09-12T10:05:00Z",
        source_revision: 1,
        processing_status: "CURRENT",
        extraction_revision: 1,
        extracted_at: "2026-09-12T10:06:00Z",
        issue_count: 0,
        task_count: 0,
        decision_count: 0,
        risk_count: 0,
        mention_count: 0,
        resolved_entity_count: 0,
      },
    ];
    expect(selectMeetingDirectory(meetings).get("mtg-1")?.title).toBe("Payment sync");
    expect(selectFreshnessTimestamp([null, "not-a-date", "2026-09-12T10:00:00Z"])).toBe(
      "2026-09-12T10:00:00Z",
    );
    expect(selectFreshnessTimestamp([null, undefined])).toBeNull();
  });
});
