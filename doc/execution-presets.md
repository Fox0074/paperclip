# Configured execution presets

Self-hosted operators can offer a configured OpenCode provider in **Agents → New agent → OpenCode → Provider setup**. The user selects a preset and a model. The first-agent onboarding screen also lists configured presets and opens this same form, preserving the company, agent name and role. The wizard uses the existing company credential. It does not request an OpenRouter account or copy an API key into browser state.

Each created agent gets its own SSH environment, container, home, OpenCode configuration, and host key. The standard agent create route still checks company access, board permissions, hire approval policy, skill assignments, instruction permissions, and budgets. Presets do not bypass pending-hire approval. If company policy requires approval, direct preset creation returns the standard approval-required error.

## Operator configuration

Set `PAPERCLIP_EXECUTION_PRESETS_FILE` to an operator-owned, read-only JSON file:

```json
{
  "presets": [{
    "id": "codex-sale",
    "companyId": "COMPANY_UUID",
    "label": "codex.sale",
    "adapterType": "opencode_local",
    "defaultModel": "codex_sale/gpt-6.1-sol",
    "models": [{ "id": "codex_sale/gpt-6.1-sol", "label": "gpt-6.1-sol" }],
    "providerBaseUrl": "https://codex.sale/v1",
    "apiKeySecretId": "COMPANY_API_KEY_SECRET_UUID",
    "sshKeySecretId": "COMPANY_SSH_KEY_SECRET_UUID",
    "brokerSocket": "/run/paperclip-provisioner/control.sock"
  }]
}
```

Replace the UUID placeholders with existing, active company-scoped Paperclip secrets. Never put key values in this file. The public API returns only preset names and model metadata. It filters entries by company. The preset test checks `/models` and provisioner readiness without paid inference. This check does not prove a model response, tool use, or provider billing.

The UI preserves other provider connections. Choose **Other provider connection** to use the standard flow. The existing agents are not migrated or changed.

## Homelab runtime provisioner

`deploy/homelab/provisioner.py` is an optional Linux host service. Paperclip contacts it through a Unix socket. Agents do not receive the socket, host SSH credentials, or the Docker socket.

Its operator-owned configuration contains a `profiles` array. Each profile has `id`, `companyId`, `homeRoot`, `projectRoot`, `image`, `network`, `authorizedKeyFile`, and `opencodeConfigFile`. Use an immutable agent image ID. Create `homeRoot` as a root-owned directory before starting the service. Use a separate profile and project directory for each company. The OpenCode JSON must reference `{env:CODEX_SALE_API_KEY}` rather than contain a key.

The service accepts only a configured company/preset pair, a generated agent UUID, and display metadata. It never accepts client commands, images, networks, mounts, keys, or paths. It creates only `paperclip-agent-<UUID>` containers. It checks ownership labels and home markers before failed-setup cleanup. Docker runs without published host ports, privileged mode, host namespace sharing, or a Docker socket mount. The SSH login uses an unprivileged `node` account.

Install the provided systemd unit only after review. Mount `/run/paperclip-provisioner` into the Paperclip control-plane container, not into agents. Mount the server preset JSON read-only. Keep the provisioner configuration root-owned. A provisioner failure is shown in the wizard; it is not reported as a successful hire.

An environment and its secret bindings are persisted atomically. If agent creation fails before the agent row exists, cleanup removes the provisional container and environment. If the agent was persisted and a later audit or response fails, its runtime is preserved. Repeated user submissions can create distinct agents, as with the standard create API. Operators should inspect the agent list before retrying a lost response.

Removing an agent does not yet remove its managed runtime. Shut down and remove the corresponding labelled container and environment through the operator after inspecting retained work. Do not use broad Docker prune commands.

## Validation and deployment

Run the preset service tests, the new-agent UI suite, and `python3 -m unittest discover -s deploy/homelab -p test_provisioner.py`. Run the repository typecheck, token gates, tests and build before handoff. Use an isolated build container without production volumes or provider credentials.

Before deployment, take a Paperclip-native database backup and preserve the current Compose configuration and image digest. Build a tagged image from the reviewed branch. Keep the previous image for rollback. The operator approves the service restart and configuration change separately from writing this patch.
