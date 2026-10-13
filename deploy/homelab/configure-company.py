"""Bind an operator-held codex.sale key to a freshly created Paperclip company."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid


def run(*args, **kwargs):
    return subprocess.check_output(list(args), text=True, **kwargs).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("company", type=lambda value: str(uuid.UUID(value)))
    parser.add_argument("--root", type=Path, default=Path("/opt/paperclip"))
    parser.add_argument("--container", default="paperclip")
    parser.add_argument("--service", default="paperclip-provisioner")
    parser.add_argument("--agent-image", default="paperclip-agent:codex-sale")
    parser.add_argument("--input-usd-per-million", type=float, required=True)
    parser.add_argument("--output-usd-per-million", type=float, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run as the host operator with sudo")
    if not all(0 < value < 100000 for value in [args.input_usd_per_million, args.output_usd_per_million]):
        parser.error("Set positive, finite provider rates; no implicit zero-cost accounting")
    root = args.root.resolve(strict=True)
    integration = root / "integration"
    api_file = integration / "codex-sale.env"
    key = api_file.read_text().strip().split("=", 1)[1]
    ssh_key = (integration / "runtime-key").read_text()
    # Values enter the container through stdin, never argv, logs, Git or the public preset.
    payload = json.dumps({"company": args.company, "api": key, "ssh": ssh_key})
    script = """
import {createDb} from '/app/packages/db/src/index.ts';
import {secretService} from '/app/server/src/services/secrets.ts';
const input = INPUT;
const db=createDb('postgres://paperclip:paperclip@127.0.0.1:54329/paperclip');
try {
 const [company]=await db.$client`select id from companies where id=${input.company}`;
 if(!company) throw Error('Create the company through Paperclip first');
 const svc=secretService(db), ids={};
 for(const [name,value] of [['CODEX_SALE_API_KEY',input.api],['CODEX_SALE_RUNTIME_SSH_KEY',input.ssh]]) {
  const [existing]=await db.$client`select id from company_secrets where company_id=${company.id} and key=${name} and status='active' and scope='company'`;
  ids[name]=existing?.id ?? (await svc.create(company.id,{name,key:name,provider:'local_encrypted',value})).id;
 }
 console.log(JSON.stringify(ids));
} finally { await db.$client.end(); }
""".replace("INPUT", payload)
    ids = json.loads(run("docker", "exec", "-u", "node", "-i", args.container, "node", "--import",
                        "/app/cli/node_modules/tsx/dist/loader.mjs", "--input-type=module", input=script))
    home = root / "agent-data" / "managed" / args.company
    home.mkdir(mode=0o700, exist_ok=True)
    workspace = root / "workspaces" / args.company
    workspace.mkdir(parents=True, exist_ok=True)
    os.chown(workspace, 1000, 1000)
    model = "gpt-6.1-sol"
    config_file = integration / (args.company + "-opencode.json")
    costs = {"input": args.input_usd_per_million, "output": args.output_usd_per_million,
             "cache_read": args.input_usd_per_million, "cache_write": args.input_usd_per_million}
    config_file.write_text(json.dumps({"enabled_providers": ["codex_sale"], "provider": {"codex_sale": {
        "npm": "@ai-sdk/openai-compatible", "name": "codex.sale", "options": {
            "baseURL": "https://codex.sale/v1", "apiKey": "{env:CODEX_SALE_API_KEY}"},
        "models": {model: {"name": model, "cost": costs, "limit": {"context": 128000, "output": 16000}}}}}}, indent=2))
    image = run("docker", "image", "inspect", args.agent_image, "--format", "{{.Id}}")
    networks = json.loads(run("docker", "inspect", args.container, "--format", "{{json .NetworkSettings.Networks}}"))
    network = next(iter(networks))
    preset = {"id": "codex-sale", "companyId": args.company, "label": "codex.sale", "adapterType": "opencode_local",
              "defaultModel": "codex_sale/" + model, "models": [{"id": "codex_sale/" + model, "label": model}],
              "providerBaseUrl": "https://codex.sale/v1", "apiKeySecretId": ids["CODEX_SALE_API_KEY"],
              "sshKeySecretId": ids["CODEX_SALE_RUNTIME_SSH_KEY"], "brokerSocket": "/run/paperclip-provisioner/control.sock"}
    profile = {"id": "codex-sale", "companyId": args.company, "homeRoot": str(home), "projectRoot": str(workspace),
               "image": image, "network": network, "authorizedKeyFile": str(integration / "runtime-key.pub"),
               "opencodeConfigFile": str(config_file)}
    for path, field, row in [(integration / "presets.json", "presets", preset),
                             (root / "onboarding/broker.json", "profiles", profile)]:
        data = json.loads(path.read_text())
        data[field] = [entry for entry in data[field] if entry["companyId"] != args.company] + [row]
        # Preserve the inode of bind-mounted presets.json. Operators must not run this concurrently.
        path.write_text(json.dumps(data, indent=2))
    run("systemctl", "restart", args.service)
    print("codex.sale configured. No agents, tasks or paid runs were created.")


if __name__ == "__main__":
    main()
