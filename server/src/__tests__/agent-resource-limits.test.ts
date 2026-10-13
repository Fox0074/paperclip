import { describe, expect, it } from "vitest";
import { operationalDayStart, observedLimitBlock, readResourceLimits } from "../services/agent-resource-limits.js";

describe("agent resource limits", () => {
  it("resets the operational day at Yekaterinburg midnight across UTC dates", () => {
    expect(operationalDayStart(new Date("2026-10-14T18:59:59Z")).toISOString()).toBe("2026-10-13T19:00:00.000Z");
    expect(operationalDayStart(new Date("2026-10-14T19:00:00Z")).toISOString()).toBe("2026-10-14T19:00:00.000Z");
  });
  it("blocks at the boundary, including explicit zero; does not block unrelated tasks", () => {
    expect(observedLimitBlock({ taskCents: 100, dailyCents: 200 }, { task: 100, daily: 20 }, "issue")).toMatch(/task/);
    expect(observedLimitBlock({ taskCents: 100 }, { task: 100, daily: 20 }, null)).toBeNull();
    expect(observedLimitBlock({ dailyCents: 0 }, { task: 0, daily: 0 })).toMatch(/daily/);
    expect(observedLimitBlock({}, { daily: 5000, task: 5000 }, "issue")).toBeNull();
  });
  it("ignores malformed and negative legacy configuration, preserves zero retries", () => {
    expect(readResourceLimits({ resourceLimits: { taskCents: -1, dailyCents: "2", maxRetries: 0 } })).toEqual({ maxRetries: 0 });
    expect(readResourceLimits(null)).toEqual({});
  });
});
