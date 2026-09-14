import { describe, expect, it } from "vitest";
import {
  actionTone,
  backendJobTone,
  entityStatusTone,
  jobStateTone,
  meetingStatusTone,
  riskTone,
} from "./status";

describe("status language", () => {
  it("covers every meeting processing state", () => {
    expect(meetingStatusTone("queued")).toBe("neutral");
    expect(meetingStatusTone("processing")).toBe("info");
    expect(meetingStatusTone("complete")).toBe("success");
    expect(meetingStatusTone("failed")).toBe("danger");
  });

  it("covers every entity resolution state", () => {
    expect(entityStatusTone("resolved")).toBe("success");
    expect(entityStatusTone("ambiguous")).toBe("warning");
    expect(entityStatusTone("unresolved")).toBe("neutral");
  });

  it("covers every risk level", () => {
    expect(riskTone("low")).toBe("neutral");
    expect(riskTone("medium")).toBe("info");
    expect(riskTone("high")).toBe("warning");
    expect(riskTone("critical")).toBe("danger");
  });

  it("covers every action state", () => {
    expect(actionTone("pending")).toBe("neutral");
    expect(actionTone("in progress")).toBe("info");
    expect(actionTone("completed")).toBe("success");
  });

  it("covers every job state on both vocabularies", () => {
    expect(jobStateTone("pending")).toBe("neutral");
    expect(jobStateTone("running")).toBe("info");
    expect(jobStateTone("retrying")).toBe("warning");
    expect(jobStateTone("succeeded")).toBe("success");
    expect(jobStateTone("failed")).toBe("danger");
    expect(jobStateTone("cancelled")).toBe("neutral");
    expect(backendJobTone("SUCCEEDED")).toBe("success");
    expect(backendJobTone("RUNNING")).toBe("info");
    expect(backendJobTone("RETRY_WAITING")).toBe("warning");
    expect(backendJobTone("FAILED")).toBe("danger");
    expect(backendJobTone("PENDING")).toBe("neutral");
    expect(backendJobTone("CANCELLED")).toBe("neutral");
  });
});
