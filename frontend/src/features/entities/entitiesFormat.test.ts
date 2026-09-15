import { describe, expect, it } from "vitest";
import type { EntityInsight, EntityResponse } from "../../types/entities";
import type { PortfolioEntitySummary } from "../../types/intelligence";
import {
  applyEntityDirectoryFilters,
  attentionReasonLabel,
  combineEntityDirectory,
  describeEntityRelationship,
  formatEntityDateTime,
  parseEntityDirectorySort,
  parseEntityDirectoryState,
  parseEntityDirectoryType,
  selectEntityMeetingsFromTemporal,
  selectLatestInsight,
  selectRiskSignals,
  temporalStateLabel,
  temporalStateTone,
} from "./entitiesFormat";

function entity(overrides: Partial<EntityResponse> = {}): EntityResponse {
  return {
    entity_id: "ent-1",
    entity_type: "ISSUE",
    canonical_name: "Payment Gateway",
    aliases: ["Payments API"],
    created_at: "2026-09-01T10:00:00Z",
    ...overrides,
  };
}

function summary(overrides: Partial<PortfolioEntitySummary> = {}): PortfolioEntitySummary {
  return {
    entity_id: "ent-1",
    entity_type: "ISSUE",
    canonical_name: "Payment Gateway",
    risk_level: "HIGH",
    attention_level: "HIGH",
    attention_score: 58,
    impact_count: 1,
    action_count: 1,
    active_insight_count: 2,
    current_state: "BLOCKED",
    observation_count: 3,
    ...overrides,
  };
}

describe("entity directory presentation controls", () => {
  it("parses only backend-supported directory controls", () => {
    expect(parseEntityDirectoryType("PERSON")).toBe("PERSON");
    expect(parseEntityDirectoryType("PROJECT")).toBe("ALL");
    expect(parseEntityDirectoryState("BLOCKED")).toBe("BLOCKED");
    expect(parseEntityDirectoryState("AT_RISK")).toBe("ALL");
    expect(parseEntityDirectorySort("observations")).toBe("observations");
    expect(parseEntityDirectorySort("popularity")).toBe("attention");
  });

  it("combines registry facts with intelligence without inventing missing assessments", () => {
    const rows = combineEntityDirectory(
      [entity(), entity({ entity_id: "ent-2", canonical_name: "Checkout", aliases: [] })],
      [summary()],
    );
    expect(rows).toHaveLength(2);
    expect(rows[0]?.summary?.attention_score).toBe(58);
    expect(rows[1]?.summary).toBeNull();
  });

  it("searches loaded names, aliases, and IDs, then filters by real state", () => {
    const rows = combineEntityDirectory(
      [
        entity(),
        entity({
          entity_id: "ent-2",
          entity_type: "PERSON",
          canonical_name: "Rahul Kumar",
          aliases: [],
        }),
      ],
      [summary(), summary({ entity_id: "ent-2", current_state: "RESOLVED", attention_level: null })],
    );
    const payment = applyEntityDirectoryFilters(rows, {
      query: "payments api",
      type: "ALL",
      state: "ALL",
      sort: "name",
    });
    expect(payment.map((row) => row.entity.entity_id)).toEqual(["ent-1"]);
    const blocked = applyEntityDirectoryFilters(rows, {
      query: "",
      type: "ALL",
      state: "BLOCKED",
      sort: "name",
    });
    expect(blocked.map((row) => row.entity.entity_id)).toEqual(["ent-1"]);
  });

  it("puts assessed attention ahead of registry-only records without inventing scores", () => {
    const rows = combineEntityDirectory(
      [
        entity({ entity_id: "ent-2", canonical_name: "Alpha Service" }),
        entity({ entity_id: "ent-1", canonical_name: "Zulu Gateway" }),
      ],
      [summary({ entity_id: "ent-1" })],
    );
    const ordered = applyEntityDirectoryFilters(rows, {
      query: "",
      type: "ALL",
      state: "ALL",
      sort: "attention",
    });
    expect(ordered.map((row) => row.entity.entity_id)).toEqual(["ent-1", "ent-2"]);
  });
});

describe("entity vocabulary and evidence selectors", () => {
  it("labels lifecycle state and attention reasons without changing their meaning", () => {
    expect(temporalStateLabel("IN_PROGRESS")).toBe("In progress");
    expect(temporalStateTone("BLOCKED")).toBe("danger");
    expect(attentionReasonLabel("REOPEN_ATTEMPT")).toBe("Reopen attempt");
    expect(formatEntityDateTime("2026-09-15T10:00:00Z")).toContain("2026");
    expect(formatEntityDateTime("not-a-date")).toBeNull();
  });

  it("describes dependency direction without turning co-occurrence into causation", () => {
    const depends = describeEntityRelationship(
      {
        relationship_id: "rel-1",
        source_entity_id: "ent-pay",
        target_entity_id: "ent-gateway",
        relationship_type: "DEPENDS_ON",
        evidence_type: "EXPLICIT_STATEMENT",
        evidence: "Explicitly stated.",
        related_meeting_ids: ["review"],
        source_text: null,
        mention_id: null,
        strength: 1,
        deterministic_sort_key: "key",
      },
      "ent-pay",
    );
    expect(depends.role).toBe("dependency");
    expect(depends.otherEntityId).toBe("ent-gateway");
  });

  it("selects real meetings and high-severity signals without inventing them", () => {
    const meetings = selectEntityMeetingsFromTemporal(
      [
        { meeting_id: "m-old", meeting_title: "Old Sync", meeting_date: "2026-09-01T10:00:00Z" },
        { meeting_id: "m-new", meeting_title: "New Sync", meeting_date: "2026-09-10T10:00:00Z" },
        { meeting_id: "m-new", meeting_title: "New Sync", meeting_date: "2026-09-10T10:00:00Z" },
      ],
      8,
    );
    expect(meetings).toEqual([
      {
        meeting_id: "m-new",
        title: "New Sync",
        meeting_date: "2026-09-10T10:00:00Z",
        observationCount: 2,
      },
      {
        meeting_id: "m-old",
        title: "Old Sync",
        meeting_date: "2026-09-01T10:00:00Z",
        observationCount: 1,
      },
    ]);
    const insights: EntityInsight[] = [
      {
        insight_id: "first",
        entity_id: "ent-1",
        insight_type: "STATE_CHANGED",
        title: "State changed",
        description: "Moved.",
        severity: "INFO",
        observed_at: "2026-09-01T10:00:00Z",
        related_meeting_id: "m-old",
        evidence: "Started.",
        deterministic_sort_key: "first",
      },
      {
        insight_id: "second",
        entity_id: "ent-1",
        insight_type: "ISSUE_BLOCKED",
        title: "Issue blocked",
        description: "Blocked.",
        severity: "WARNING",
        observed_at: "2026-09-10T10:00:00Z",
        related_meeting_id: "m-new",
        evidence: "Blocked.",
        deterministic_sort_key: "second",
      },
    ];
    expect(selectLatestInsight(insights)?.insight_id).toBe("second");
    expect(selectRiskSignals(insights).map((insight) => insight.insight_id)).toEqual(["second"]);
  });
});
