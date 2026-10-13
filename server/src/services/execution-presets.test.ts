import { afterEach, describe, expect, it, vi } from "vitest";
import { createServer } from "node:http";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { configuredExecutionPresets, executionPresetConfigSchema, getExecutionPreset, publicExecutionPreset, prepareExecutionPreset, checkExecutionPreset } from "./execution-presets.js";
const secretMock = vi.hoisted(() => ({ resolveSecretValue: vi.fn(), replaceSecretRefsForInstanceTarget: vi.fn() }));
vi.mock("./secrets.js", () => ({ secretService: () => secretMock }));
const envMock = vi.hoisted(() => ({ create: vi.fn(), removeIfDeletable: vi.fn() }));
vi.mock("./environments.js", () => ({ environmentService: () => envMock }));

const companyId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const preset = { id: "codex-sale", companyId, label: "codex.sale", adapterType: "opencode_local",
  defaultModel: "codex_sale/gpt-6.1-sol", models: [{ id: "codex_sale/gpt-6.1-sol", label: "gpt-6.1-sol" }],
  providerBaseUrl: "https://codex.sale/v1", apiKeySecretId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  sshKeySecretId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", brokerSocket: "/run/paperclip-provisioner/control.sock" } as const;
let directory: string | undefined;
afterEach(async () => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.clearAllMocks(); if (directory) await rm(directory, { recursive: true }); directory = undefined; });

describe("execution preset boundaries", () => {
  async function configure() {
    directory = await mkdtemp(path.join(tmpdir(), "preset-test-"));
    const file = path.join(directory, "presets.json");
    await writeFile(file, JSON.stringify({ presets: [preset] }));
    vi.stubEnv("PAPERCLIP_EXECUTION_PRESETS_FILE", file);
  }
  const input = { companyId, agentId: "11111111-1111-4111-8111-111111111111", presetId: "codex-sale",
    model: preset.defaultModel, name: "Engineer", role: "engineer" };

  it("audits a board probe without requiring an agent binding or a config path", async () => {
    await configure();
    const socket = path.join(directory!, "broker.sock");
    await writeFile(path.join(directory!, "presets.json"), JSON.stringify({ presets: [{ ...preset, brokerSocket: socket }] }));
    secretMock.resolveSecretValue.mockResolvedValue("synthetic-provider-key");
    const fetch = vi.fn(async (_url: string, _init: unknown) => ({ ok: true, json: async () => ({ data: [{ id: "gpt-6.1-sol" }] }) }));
    vi.stubGlobal("fetch", fetch);
    const broker = createServer((_request, response) => response.end('{"ready":true}'));
    await new Promise<void>(resolve => broker.listen(socket, resolve));
    const context = { consumerType: "system" as const, consumerId: "execution-preset-probe",
      actorType: "user" as const, actorId: "synthetic-owner", actorSource: "session" as const };
    try {
      const result = await checkExecutionPreset({} as never, companyId, preset.id, context);
      expect(result.status).toBe("pass");
      expect(secretMock.resolveSecretValue).toHaveBeenCalledWith(companyId, preset.apiKeySecretId,
        "latest", { accessContext: context });
      expect(fetch.mock.calls[0]?.[0]).toBe("https://codex.sale/v1/models");
    } finally { await new Promise<void>(resolve => broker.close(() => resolve())); }
  });

  it("does not provision a runtime when a company credential is unavailable or the model is invalid", async () => {
    await configure();
    const db = { select: () => ({ from: () => ({ where: async () => [] }) }) };
    const broker = vi.fn();
    await expect(prepareExecutionPreset(db as never, input, broker)).rejects.toThrow("credential is unavailable");
    await expect(prepareExecutionPreset(db as never, { ...input, model: "other/model" }, broker)).rejects.toThrow("Choose a model");
    expect(broker).not.toHaveBeenCalled();
  });

  it("removes a just-provisioned container when persisting its environment fails", async () => {
    await configure();
    const db = { select: () => ({ from: () => ({ where: async () => [{ id: preset.apiKeySecretId }] }) }),
      transaction: async (callback: (tx: unknown) => unknown) => callback({}) };
    envMock.create.mockRejectedValueOnce(new Error("database write failed"));
    const broker = vi.fn(async (_preset: unknown, _method: string, _route: string) => ({
      host: "paperclip-agent-test", knownHosts: "paperclip-agent-test ssh-ed25519 public-key" }));
    await expect(prepareExecutionPreset(db as never, input, broker)).rejects.toThrow("database write failed");
    expect(broker.mock.calls.map(call => call[2])).toEqual(["/provision", "/remove"]);
  });

  it("returns no credentials or host management settings in public metadata", () => {
    const parsed = executionPresetConfigSchema.parse({ presets: [preset] }).presets[0]!;
    const exposed = JSON.stringify(publicExecutionPreset(parsed));
    expect(exposed).not.toContain(preset.apiKeySecretId);
    expect(exposed).not.toContain(preset.sshKeySecretId);
    expect(exposed).not.toContain(preset.brokerSocket);
    expect(exposed).not.toContain(preset.companyId);
  });
  it("does not list or select another company's preset", async () => {
    directory = await mkdtemp(path.join(tmpdir(), "preset-test-"));
    const file = path.join(directory, "presets.json");
    await writeFile(file, JSON.stringify({ presets: [preset] }));
    vi.stubEnv("PAPERCLIP_EXECUTION_PRESETS_FILE", file);
    expect(await configuredExecutionPresets(companyId)).toHaveLength(1);
    expect(await configuredExecutionPresets("22222222-2222-4222-8222-222222222222")).toEqual([]);
    await expect(getExecutionPreset("22222222-2222-4222-8222-222222222222", "codex-sale")).rejects.toThrow("not configured");
  });
  it("fails closed on raw credentials, HTTP endpoints, duplicate presets, and missing default models", () => {
    for (const config of [
      { presets: [{ ...preset, apiKey: "unexpected-raw-key" }] },
      { presets: [{ ...preset, providerBaseUrl: "http://localhost/v1" }] },
      { presets: [preset, preset] },
      { presets: [{ ...preset, defaultModel: "missing" }] },
    ]) expect(executionPresetConfigSchema.safeParse(config).success).toBe(false);
  });
});
