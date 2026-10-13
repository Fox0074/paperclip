import { and, eq, sql } from "drizzle-orm";
import { costEvents, type Db } from "@paperclipai/db";

export type ResourceLimits = { taskCents?: number; dailyCents?: number; maxRetries?: number };

export function readResourceLimits(runtime: unknown): ResourceLimits {
  if (!runtime || typeof runtime !== "object") return {};
  const raw = (runtime as Record<string, unknown>).resourceLimits;
  if (!raw || typeof raw !== "object") return {};
  const result: ResourceLimits = {};
  for (const key of ["taskCents", "dailyCents", "maxRetries"] as const) {
    const value = (raw as Record<string, unknown>)[key];
    if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) result[key] = value;
  }
  return result;
}

// Company accounting remains UTC monthly; the owner's operational day is UTC+5.
export function operationalDayStart(now = new Date()): Date {
  const offset = 5 * 60 * 60 * 1000;
  const local = new Date(now.getTime() + offset);
  return new Date(Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate()) - offset);
}

export function observedLimitBlock(limits: ResourceLimits, spend: { daily: number; task: number }, issueId?: string | null) {
  if (limits.dailyCents !== undefined && spend.daily >= limits.dailyCents) return "Agent's daily recorded-spend limit is reached (Asia/Yekaterinburg).";
  if (issueId && limits.taskCents !== undefined && spend.task >= limits.taskCents) return "Agent's recorded-spend limit for this task is reached.";
  return null;
}

export async function getAgentResourceBlock(db: Db, input: {
  companyId: string; agentId: string; runtimeConfig: unknown; issueId?: string | null;
}) {
  const limits = readResourceLimits(input.runtimeConfig);
  if (limits.dailyCents === undefined && limits.taskCents === undefined) return null;
  const [row] = await db.select({
    daily: sql<number>`coalesce(sum(case when ${costEvents.occurredAt} >= ${operationalDayStart().toISOString()} then ${costEvents.costCents} else 0 end),0)::double precision`,
    task: input.issueId
      ? sql<number>`coalesce(sum(case when ${costEvents.issueId} = ${input.issueId} then ${costEvents.costCents} else 0 end),0)::double precision`
      : sql<number>`0`,
  }).from(costEvents).where(and(eq(costEvents.companyId, input.companyId), eq(costEvents.agentId, input.agentId)));
  return observedLimitBlock(limits, { daily: Number(row?.daily ?? 0), task: Number(row?.task ?? 0) }, input.issueId);
}
