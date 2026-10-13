#!/usr/bin/env python3
"""Install the pinned Paperclip fork and its isolated codex.sale runtimes."""
import argparse
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request

REPO = Path(__file__).resolve().parents[2]
MARKER = "codex-sale-installation.json"


def run(*args, capture=False, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, text=True,
                          stdout=subprocess.PIPE if capture else None, **kwargs).stdout


def write_json(path, value, mode=0o600):
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(mode)


def claim_root(root, commit, host, port):
    """Claim only an empty directory; preserve existing installs and their secrets."""
    if root.is_symlink():
        raise ValueError("Installation root must not be a symbolic link")
    marker = root / MARKER
    if marker.is_file():
        manifest = json.loads(marker.read_text())
        if manifest.get("format") != 1 or manifest.get("root") != str(root):
            raise ValueError("Installation ownership marker does not match")
        if manifest["host"] != host or manifest["port"] != port:
            raise ValueError("Host/port differ from this installation; keep the original arguments")
        if manifest["commit"] != commit:
            raise ValueError("Source changed. Use a separate root/port to test the new version first")
        return manifest, False
    if root.exists() and any(root.iterdir()):
        raise ValueError("Directory contains an existing installation. Nothing will be overwritten or deleted")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    root.chmod(0o700)
    manifest = {"format": 1, "root": str(root), "commit": commit,
                "host": host, "port": port, "configuredCompanies": []}
    write_json(marker, manifest)
    return manifest, True


def compose_config(root, host, port, image):
    name = root.name
    return {"services": {"paperclip": {
        "image": image, "container_name": name, "restart": "unless-stopped",
        "stop_grace_period": "90s", "ports": [f"{host}:{port}:3100"],
        "environment": {
            "HOST": "0.0.0.0", "PORT": "3100", "SERVE_UI": "true",
            "PAPERCLIP_TELEMETRY_DISABLED": "1", "PAPERCLIP_HOME": "/paperclip",
            "PAPERCLIP_DEPLOYMENT_MODE": "authenticated", "PAPERCLIP_DEPLOYMENT_EXPOSURE": "private",
            "PAPERCLIP_PUBLIC_URL": f"http://{host}:{port}",
            "PAPERCLIP_AUTH_PUBLIC_BASE_URL": f"http://{host}:{port}",
            "BETTER_AUTH_URL": f"http://{host}:{port}",
            "BETTER_AUTH_TRUSTED_ORIGINS": f"http://{host}:{port}", "PAPERCLIP_ALLOWED_HOSTNAMES": host,
            "PAPERCLIP_EXECUTION_PRESETS_FILE": "/integration/presets.json",
            "BETTER_AUTH_SECRET": "${BETTER_AUTH_SECRET}",
            "PAPERCLIP_AGENT_JWT_SECRET": "${PAPERCLIP_AGENT_JWT_SECRET}"},
        "volumes": [f"{root}/data:/paperclip", f"{root}/workspace:/workspace",
                    f"{root}/integration/presets.json:/integration/presets.json:ro",
                    f"/run/{name}-provisioner:/run/paperclip-provisioner"]}}}


def systemd_unit(root):
    name = root.name
    return f"""[Unit]
Description=Paperclip codex.sale isolated runtime provisioner ({name})
After=docker.service
Requires=docker.service

[Service]
ExecStart=/usr/bin/python3 {root}/onboarding/provisioner.py --config {root}/onboarding/broker.json --socket /run/{name}-provisioner/control.sock
Restart=on-failure
RuntimeDirectory={name}-provisioner
RuntimeDirectoryMode=0755
RuntimeDirectoryPreserve=yes
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={root}/agent-data/managed

[Install]
WantedBy=multi-user.target
"""


def initialize(root, host, port):
    for path in [root / "integration", root / "onboarding", root / "agent-data/managed"]:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if not (root / "integration/presets.json").exists():
        write_json(root / "integration/presets.json", {"presets": []}, 0o644)
    if not (root / "onboarding/broker.json").exists():
        write_json(root / "onboarding/broker.json", {"profiles": []})
    for path in [root / "data", root / "data/instances", root / "data/instances/default", root / "workspace"]:
        path.mkdir(mode=0o700, exist_ok=True)
        os.chown(path, 1000, 1000)
    config = {
        "$meta": {"version": 1, "source": "configure", "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        "database": {"mode": "embedded-postgres", "embeddedPostgresDataDir": "/paperclip/instances/default/db",
                     "embeddedPostgresPort": 54329, "backup": {"enabled": True, "intervalMinutes": 60,
                     "retentionDays": 7, "dir": "/paperclip/instances/default/data/backups"}},
        "logging": {"mode": "file", "logDir": "/paperclip/instances/default/logs"},
        "server": {"deploymentMode": "authenticated", "exposure": "private", "host": "0.0.0.0",
                   "port": 3100, "allowedHostnames": [host], "serveUi": True},
        "telemetry": {"enabled": False}, "auth": {"baseUrlMode": "explicit",
                   "publicBaseUrl": f"http://{host}:{port}", "disableSignUp": False},
        "storage": {"provider": "local_disk", "localDisk": {"baseDir": "/paperclip/instances/default/data/storage"}},
        "secrets": {"provider": "local_encrypted", "strictMode": False,
                    "localEncrypted": {"keyFilePath": "/paperclip/instances/default/secrets/master.key"}}}
    path = root / "data/instances/default/config.json"
    if not path.exists():
        write_json(path, config)
        os.chown(path, 1000, 1000)
    env = root / ".env"
    if not env.exists():
        env.write_text("BETTER_AUTH_SECRET=" + secrets.token_hex(32) + "\n"
                       + "PAPERCLIP_AGENT_JWT_SECRET=" + secrets.token_hex(32) + "\n")
        env.chmod(0o600)
    if not (root / "integration/runtime-key").exists():
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", root / "integration/runtime-key")


def save_provider_key(root, key_file=None):
    target = root / "integration/codex-sale.env"
    if target.exists():
        return  # Rotation belongs to Paperclip, not an idempotent installation run.
    key = key_file.read_text().strip() if key_file else getpass.getpass("codex.sale API key (hidden): ").strip()
    if len(key) < 20 or "\n" in key or "\r" in key or "=" in key:
        raise ValueError("Provide the raw API key, not an env file")
    target.write_text("CODEX_SALE_API_KEY=" + key + "\n")
    target.chmod(0o600)


def node(root, script):
    return run("docker", "exec", "-i", root.name, "node", "--import",
               "/app/cli/node_modules/tsx/dist/loader.mjs", "--input-type=module", capture=True, input=script)


def wait_health(url, commit, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=5) as response:
                result = json.load(response)
            if result.get("status") == "ok" and result.get("commit") == commit:
                return result
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError("Paperclip did not become ready. Inspect docker logs; existing data has been preserved")


def configure(root, manifest, args):
    companies = json.loads(node(root, """
import {createDb} from '/app/packages/db/src/index.ts';
const db=createDb('postgres://paperclip:paperclip@127.0.0.1:54329/paperclip');
console.log(JSON.stringify(await db.$client`select id,name,budget_monthly_cents from companies order by created_at`));
await db.$client.end();
"""))
    if not companies:
        raise ValueError("Create your owner account and a company in the browser first; rerun with --configure")
    if args.company:
        company = next((entry for entry in companies if entry["id"] == args.company), None)
        if company is None:
            raise ValueError("Company was not found in this installation")
    elif len(companies) == 1:
        company = companies[0]
    else:
        raise ValueError("Multiple companies exist. Rerun with --company COMPANY_UUID")
    rates = [args.input_rate, args.output_rate]
    for index, label in enumerate(["Input", "Output"]):
        if rates[index] is None:
            rates[index] = float(input(f"{label} price in USD per million tokens: "))
        if not 0 < rates[index] < 100000:
            raise ValueError("Rates must be positive and finite; confirm them with codex.sale")
    run(sys.executable, root / "onboarding/configure-company.py", company["id"], "--root", root,
        "--container", root.name, "--service", root.name + "-provisioner",
        "--agent-image", root.name + "-agent:codex-sale",
        "--input-usd-per-million", rates[0], "--output-usd-per-million", rates[1])
    if company["id"] not in manifest["configuredCompanies"]:
        if company["budget_monthly_cents"] == 0:
            node(root, """
import {createDb} from '/app/packages/db/src/index.ts';
import {companyService} from '/app/server/src/services/companies.ts';
const db=createDb('postgres://paperclip:paperclip@127.0.0.1:54329/paperclip');
await companyService(db).update(COMPANY,{budgetMonthlyCents:BUDGET});
await db.$client.end();
""".replace("COMPANY", json.dumps(company["id"])).replace("BUDGET", str(round(args.budget_usd * 100))))
        manifest["configuredCompanies"].append(company["id"])
        write_json(root / MARKER, manifest)
    result = json.loads(node(root, """
import {createDb} from '/app/packages/db/src/index.ts';
import {checkExecutionPreset} from '/app/server/src/services/execution-presets.ts';
const db=createDb('postgres://paperclip:paperclip@127.0.0.1:54329/paperclip');
try {const r=await checkExecutionPreset(db,COMPANY,'codex-sale');console.log(JSON.stringify({status:r.status}));}
finally {await db.$client.end();}
""".replace("COMPANY", json.dumps(company["id"]))))
    if result.get("status") != "pass":
        raise RuntimeError("Provider readiness check did not pass")
    print("Ready: codex.sale is available in first-agent onboarding and Agents → New agent.")
    print("No agents, tasks or paid inference runs were created. Billing is calculated from your supplied rates.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, type=lambda value: str(ipaddress.IPv4Address(value)))
    parser.add_argument("--port", type=int, default=3100)
    parser.add_argument("--root", type=Path, default=Path("/opt/paperclip"))
    parser.add_argument("--key-file", type=Path, help="Protected file containing the raw API key")
    parser.add_argument("--input-rate", type=float, help="USD per million input tokens")
    parser.add_argument("--output-rate", type=float, help="USD per million output tokens")
    parser.add_argument("--budget-usd", type=float, default=50)
    parser.add_argument("--company")
    parser.add_argument("--no-wait", action="store_true", help="Return before browser account/company setup")
    parser.add_argument("--configure", action="store_true", help="Bind a company without rebuilding or restarting Paperclip")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or not 0.01 <= args.budget_usd < 1000000:
        parser.error("Set a port between 1024 and 65535 and a positive finite budget")
    if ipaddress.IPv4Address(args.host).is_unspecified or ipaddress.IPv4Address(args.host).is_multicast:
        parser.error("Use the server's actual IPv4 address, not a wildcard or multicast address")
    if args.key_file:
        args.key_file = args.key_file.resolve(strict=True)
        if args.key_file.is_relative_to(REPO) or args.key_file.stat().st_mode & 0o077:
            parser.error("Keep the raw key file outside the repository with permissions 0600")
    if not args.root.is_absolute() or args.root.parent != Path("/opt") or not re.fullmatch(r"paperclip(?:-[a-z0-9-]+)?", args.root.name):
        parser.error("Root must be /opt/paperclip or /opt/paperclip-NAME")
    if sys.platform != "linux" or os.geteuid() != 0:
        parser.error("Run on Ubuntu with sudo python3 deploy/codex-sale/install.py")
    for command in ["docker", "git", "ssh-keygen", "systemctl"]:
        if not shutil.which(command):
            parser.error(f"Missing {command}; install the prerequisites in README.ru.md")
    run("docker", "compose", "version", capture=True)
    run("docker", "info", capture=True)
    commit = run("git", "-C", REPO, "rev-parse", "HEAD", capture=True).strip()
    if run("git", "--no-optional-locks", "-C", REPO, "status", "--porcelain", "--untracked-files=no", capture=True).strip():
        parser.error("Use a clean clone with committed source so image versions can be verified")
    if args.configure and not (args.root / MARKER).is_file():
        parser.error("No installer-managed installation exists at this root")
    manifest, fresh = claim_root(args.root, commit, args.host, args.port)
    if not args.configure:
        initialize(args.root, args.host, args.port)
    save_provider_key(args.root, args.key_file)
    name = args.root.name
    service = name + "-provisioner"
    url = f"http://{args.host}:{args.port}"
    if not args.configure:
        # Do not overwrite a system service or container owned by another installation.
        unit = Path("/etc/systemd/system") / (service + ".service")
        expected = systemd_unit(args.root)
        if unit.exists() and unit.read_text() != expected:
            raise ValueError("Existing provisioner service differs; use a separate root/port")
        existing = subprocess.run(["docker", "inspect", name], text=True, capture_output=True)
        if existing.returncode == 0:
            container = json.loads(existing.stdout)[0]
            labels = container["Config"].get("Labels") or {}
            if labels.get("com.docker.compose.project") != name:
                raise ValueError("A container with this name belongs to another deployment")
        for file in ["provisioner.py", "configure-company.py"]:
            shutil.copy2(REPO / "deploy/homelab" / file, args.root / "onboarding" / file)
        image = "paperclip-codex-sale:" + commit[:12]
        run("docker", "build", "-f", REPO / "deploy/homelab/Dockerfile.paperclip",
            "--build-arg", "PAPERCLIP_BUILD_COMMIT=" + commit, "-t", image, REPO)
        run("docker", "build", "-f", REPO / "deploy/homelab/Dockerfile.agent",
            "-t", name + "-agent:codex-sale", REPO)
        write_json(args.root / "compose.json", compose_config(args.root, args.host, args.port, image))
        unit.write_text(expected)
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", "--now", service)
        run("docker", "compose", "-f", args.root / "compose.json", "--env-file", args.root / ".env",
            "-p", name, "up", "-d")
    health = wait_health(url, commit)
    if health.get("bootstrapStatus") == "bootstrap_pending":
        invite_file = args.root / "owner-invite.txt"
        if not invite_file.exists() or time.time() - invite_file.stat().st_mtime >= 72 * 3600:
            result = run("docker", "exec", name, "node", "/app/cli/node_modules/tsx/dist/cli.mjs",
                         "/app/cli/src/index.ts", "auth", "bootstrap-ceo", "--base-url", url,
                         "--expires-hours", "72", "--force", capture=True)
            clean = re.sub(r"\x1b\[[0-9;]*m", "", result)
            match = re.search(r"https?://[^\s]+/invite/pcp_bootstrap_[a-f0-9]+", clean)
            if not match:
                raise RuntimeError("Could not obtain the owner invitation; inspect CLI output locally")
            invite_file.write_text(match.group() + "\n")
            invite_file.chmod(0o600)
        print("Create your owner account:", invite_file.read_text().strip())
    print("Paperclip:", url)
    if args.no_wait:
        print("After creating a company, rerun this command with --configure and the same host/root/port.")
        return
    if not args.configure:
        input("Create the owner account and an empty company in the browser, then press Enter: ")
    configure(args.root, manifest, args)


if __name__ == "__main__":
    os.umask(0o077)
    try:
        main()
    except (ValueError, RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print("Installation stopped:", str(error), file=sys.stderr)
        sys.exit(1)
