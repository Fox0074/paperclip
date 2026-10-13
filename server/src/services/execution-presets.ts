import { readFile } from "node:fs/promises";
import { request } from "node:http";
import { z } from "zod";
import { and, eq } from "drizzle-orm";
import { companySecrets } from "@paperclipai/db";
import type { Db } from "@paperclipai/db";
import type { ExecutionPreset } from "@paperclipai/shared";
import { notFound, unprocessable } from "../errors.js";
import { environmentService } from "./environments.js";
import { secretService } from "./secrets.js";
import { collectEnvironmentSecretRefs } from "./environment-config.js";

export const executionPresetConfigSchema = z.object({
  presets: z.array(z.object({
    id: z.string().regex(/^[a-z0-9][a-z0-9-]*$/),
    companyId: z.string().guid(),
    label: z.string().min(1),
    adapterType: z.literal("opencode_local"),
    defaultModel: z.string().min(1),
    models: z.array(z.object({ id: z.string().min(1), label: z.string().min(1) })).min(1),
    providerBaseUrl: z.url().refine(value => new URL(value).protocol === "https:"),
    apiKeySecretId: z.string().guid(),
    sshKeySecretId: z.string().guid(),
    brokerSocket: z.string().startsWith("/"),
  }).strict()).superRefine((presets, ctx) => {
    const keys = new Set<string>();
    for (const preset of presets) {
      const key = `${preset.companyId}:${preset.id}`;
      if (keys.has(key) || !preset.models.some(model => model.id === preset.defaultModel)) {
        ctx.addIssue({ code: "custom", message: "Duplicate preset or unavailable default model" });
      }
      keys.add(key);
    }
  }),
}).strict();

type ConfiguredPreset = z.infer<typeof executionPresetConfigSchema>["presets"][number];

export function publicExecutionPreset(preset: ConfiguredPreset): ExecutionPreset {
  return { id: preset.id, label: preset.label, adapterType: preset.adapterType,
    defaultModel: preset.defaultModel, models: preset.models };
}

export async function configuredExecutionPresets(companyId: string) {
  const file = process.env.PAPERCLIP_EXECUTION_PRESETS_FILE;
  if (!file) return [];
  const config = executionPresetConfigSchema.parse(JSON.parse(await readFile(file, "utf8")));
  return config.presets.filter(preset => preset.companyId === companyId);
}

export async function getExecutionPreset(companyId: string, id: string) {
  const preset = (await configuredExecutionPresets(companyId)).find(entry => entry.id === id);
  if (!preset) throw notFound("Execution preset is not configured for this company");
  return preset;
}

export function presetBrokerRequest(preset: ConfiguredPreset, method: string, route: string,
  body?: Record<string, unknown>): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const req = request({ socketPath: preset.brokerSocket, path: route, method,
      headers: { "Content-Type": "application/json" }, timeout: 60_000 }, res => {
      let text = "";
      res.setEncoding("utf8");
      res.on("data", chunk => {
        text += chunk;
        if (text.length > 65_536) req.destroy(new Error("Invalid broker response"));
      });
      res.on("end", () => {
        if (res.statusCode !== 200) return reject(unprocessable("Isolated runtime provisioning failed; check the provisioner service"));
        try { resolve(JSON.parse(text)); } catch { reject(new Error("Invalid broker response")); }
      });
    });
    req.on("timeout", () => req.destroy(new Error("Isolated runtime provisioner timed out")));
    req.on("error", reject);
    req.end(body ? JSON.stringify(body) : undefined);
  });
}

export async function prepareExecutionPreset(db: Db, input: {
  companyId: string; agentId: string; presetId: string; model: unknown; name: string; role: string;
}, broker = presetBrokerRequest) {
  const preset = await getExecutionPreset(input.companyId, input.presetId);
  const model = typeof input.model === "string" && input.model ? input.model : preset.defaultModel;
  if (!preset.models.some(entry => entry.id === model)) throw unprocessable("Choose a model offered by the execution preset");
  // Never bind a credential from another company or a user's private connection.
  for (const secretId of [preset.apiKeySecretId, preset.sshKeySecretId]) {
    const [secret] = await db.select().from(companySecrets).where(and(
      eq(companySecrets.id, secretId), eq(companySecrets.companyId, input.companyId),
      eq(companySecrets.scope, "company"), eq(companySecrets.status, "active"),
    ));
    if (!secret) throw unprocessable("The execution preset's company credential is unavailable");
  }
  const envs = environmentService(db);
  const brokerInput = { companyId: input.companyId, agentId: input.agentId, presetId: input.presetId,
    name: input.name, role: input.role };
  let environmentId: string | null = null;
  const cleanup = async () => {
    await broker(preset, "POST", "/remove", brokerInput);
    if (environmentId) {
      await secretService(db).replaceSecretRefsForInstanceTarget(
        { targetType: "environment", targetId: environmentId }, [],
      );
      await envs.removeIfDeletable(environmentId);
    }
  };
  try {
    const runtime = z.object({ host: z.string().min(1), knownHosts: z.string().min(1) })
      .parse(await broker(preset, "POST", "/provision", brokerInput));
    const environmentInput = {
      name: `Isolated ${input.agentId}`, driver: "ssh", status: "active", envVars: {},
      config: { host: runtime.host, port: 22, username: "node", remoteWorkspacePath: "/workspace",
        privateKeySecretRef: { type: "secret_ref", secretId: preset.sshKeySecretId, version: "latest" },
        knownHosts: runtime.knownHosts, strictHostKeyChecking: true },
      metadata: { executionPresetId: preset.id, companyId: input.companyId, agentId: input.agentId },
    } as const;
    const environment = await db.transaction(async tx => {
      const created = await envs.create(environmentInput, undefined, { db: tx });
      await secretService(db).replaceSecretRefsForInstanceTarget(
        { targetType: "environment", targetId: created.id },
        await collectEnvironmentSecretRefs({ db, environment: created }), { db: tx },
      );
      return created;
    });
    environmentId = environment.id;
    return { environmentId, cleanup, adapterConfig: {
      cwd: "/workspace", command: "opencode", model, instructionsFilePath: "/paperclip/AGENT.md",
      env: { CODEX_SALE_API_KEY: { type: "secret_ref", secretId: preset.apiKeySecretId, version: "latest" },
        OPENCODE_CONFIG: "/paperclip/.config/opencode/opencode.json" },
    } };
  } catch (error) {
    await cleanup().catch(() => undefined);
    throw error;
  }
}

export async function checkExecutionPreset(db: Db, companyId: string, id: string,
  context?: Parameters<ReturnType<typeof secretService>["resolveSecretValue"]>[3]) {
  const preset = await getExecutionPreset(companyId, id);
  const key = await secretService(db).resolveSecretValue(companyId, preset.apiKeySecretId, "latest", context);
  const response = await fetch(`${preset.providerBaseUrl.replace(/\/$/, "")}/models`, {
    headers: { Authorization: `Bearer ${key}` }, signal: AbortSignal.timeout(20_000), redirect: "error",
  });
  if (!response.ok) throw unprocessable(`Provider model discovery failed (HTTP ${response.status})`);
  const body = z.object({ data: z.array(z.object({ id: z.string() })) }).parse(await response.json());
  const missing = preset.models.filter(model => !body.data.some(entry => model.id.endsWith(`/${entry.id}`)));
  if (missing.length) throw unprocessable("A configured model is no longer available from the provider");
  await presetBrokerRequest(preset, "GET", "/health");
  return { adapterType: "opencode_local", status: "pass", testedAt: new Date().toISOString(), checks: [
    { code: "preset_provider", level: "info", message: "Saved company credential resolved and provider model catalog checked. No inference call was made." },
    { code: "preset_runtime", level: "info", message: "Isolated runtime provisioner is ready. A new container will be created when you finish setup." },
  ] };
}
