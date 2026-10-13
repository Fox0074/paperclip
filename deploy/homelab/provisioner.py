"""Narrow host-side container provisioning. No shell, arbitrary mounts, or Docker socket in agents."""
import argparse
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import socketserver
import subprocess
import threading

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
LABEL = "paperclip.execution-preset.agent-id"
LOCK = threading.Lock()


def validate_request(value, profiles):
    if not isinstance(value, dict) or set(value) != {"companyId", "agentId", "presetId", "name", "role"}:
        raise ValueError("Invalid provisioning request")
    if not all(isinstance(value[k], str) for k in ["agentId", "companyId"]):
        raise ValueError("Invalid identity")
    if not UUID.fullmatch(value["agentId"]) or not UUID.fullmatch(value["companyId"]):
        raise ValueError("Invalid identity")
    if not all(isinstance(value[k], str) and len(value[k]) <= 256 for k in ["name", "role", "presetId"]):
        raise ValueError("Invalid agent metadata")
    profile = next((p for p in profiles if p["companyId"] == value["companyId"] and p["id"] == value["presetId"]), None)
    if profile is None:
        raise ValueError("Preset unavailable for this company")
    return profile


def managed_home(profile, agent_id):
    if not UUID.fullmatch(agent_id):
        raise ValueError("Invalid agent identity")
    root = Path(profile["homeRoot"]).resolve(strict=True)
    path = root / agent_id
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError("Managed home must be inside its configured root")
    return path


def docker(*args):
    return subprocess.check_output(["/usr/bin/docker", *args], text=True, stderr=subprocess.DEVNULL, timeout=45).strip()


def inspect(name):
    try:
        return json.loads(docker("inspect", name))[0]
    except subprocess.CalledProcessError:
        return None


def remove(profile, value):
    name = "paperclip-agent-" + value["agentId"]
    current = inspect(name)
    if current:
        if current["Config"]["Labels"].get(LABEL) != value["agentId"]:
            raise ValueError("Refusing to remove an unmanaged container")
        docker("rm", "-f", name)
    home = managed_home(profile, value["agentId"])
    marker = home / ".paperclip-managed.json"
    if home.exists():
        if not marker.is_file() or json.loads(marker.read_text())["agentId"] != value["agentId"]:
            raise ValueError("Refusing to remove an unmanaged home")
        shutil.rmtree(home)


def provision(profile, value):
    name = "paperclip-agent-" + value["agentId"]
    home = managed_home(profile, value["agentId"])
    current = inspect(name)
    if current and current["Config"]["Labels"].get(LABEL) != value["agentId"]:
        raise ValueError("Container identity collision")
    if not current:
        home.mkdir(mode=0o700)
        (home / ".paperclip-managed.json").write_text(json.dumps({"agentId": value["agentId"]}))
        ssh = home / ".ssh"
        ssh.mkdir(mode=0o700)
        (ssh / "authorized_keys").write_bytes(Path(profile["authorizedKeyFile"]).read_bytes())
        (ssh / "authorized_keys").chmod(0o600)
        config = home / ".config" / "opencode"
        config.mkdir(parents=True)
        (config / "opencode.json").write_bytes(Path(profile["opencodeConfigFile"]).read_bytes())
        (home / "AGENT.md").write_text(
            f"You are {value['name']}, role {value['role']}. Follow the instructions and assignments configured in Paperclip. "
            "Use the Paperclip skill and API for assignment, collaboration and tangible completion. "
            "Protect secrets and respect the shared company budget. Production deployment and external human "
            "outreach require the owner's specific approval.\n"
        )
        for path in [home, *home.rglob("*")]:
            os.chown(path, 1000, 1000)
        # Each new container gets independent host keys, even when its image was built with sshd installed.
        start = "rm -f /etc/ssh/ssh_host_ed25519_key /etc/ssh/ssh_host_ed25519_key.pub /etc/ssh/ssh_host_rsa_key /etc/ssh/ssh_host_rsa_key.pub /etc/ssh/ssh_host_ecdsa_key /etc/ssh/ssh_host_ecdsa_key.pub; ssh-keygen -A && exec /usr/sbin/sshd -D -e"
        docker("run", "-d", "--name", name, "--restart", "unless-stopped", "--network", profile["network"],
               "--label", f"{LABEL}={value['agentId']}", "--label", "paperclip.execution-preset.company-id=" + value["companyId"],
               "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
               "--cap-add", "SETUID", "--cap-add", "SETGID", "--cap-add", "SYS_CHROOT",
               "--cap-add", "CHOWN", "--cap-add", "DAC_OVERRIDE", "--cap-add", "AUDIT_WRITE",
               "--mount", f"type=bind,src={home},dst=/paperclip",
               "--mount", f"type=bind,src={Path(profile['projectRoot']).resolve(strict=True)},dst=/workspace",
               "--entrypoint", "/bin/sh", profile["image"], "-c", start)
    import time
    key = None
    for _ in range(30):
        try:
            key = docker("exec", name, "cat", "/etc/ssh/ssh_host_ed25519_key.pub")
            if key:
                docker("exec", name, "node", "-e",
                       "const s=require('net').connect(22,'127.0.0.1');s.setTimeout(1000);s.on('connect',()=>{s.end();process.exit(0)});s.on('error',()=>process.exit(1));s.on('timeout',()=>process.exit(1))")
                break
        except subprocess.CalledProcessError:
            pass
        time.sleep(0.25)
    if not key:
        raise RuntimeError("Runtime host key unavailable")
    return {"host": name, "knownHosts": name + " " + " ".join(key.split()[:2])}


def serve(config, socket_path):
    if not hasattr(socketserver, "UnixStreamServer"):
        raise RuntimeError("The host provisioner requires Linux Unix sockets")

    class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    profiles = config["profiles"]

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, value):
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path != "/health":
                return self.reply(404, {"error": "Unknown operation"})
            try:
                for profile in profiles:
                    docker("image", "inspect", profile["image"])
                    docker("network", "inspect", profile["network"])
                self.reply(200, {"ready": True})
            except Exception:
                self.reply(503, {"error": "Provisioner is not ready"})

        def do_POST(self):
            if self.path not in ["/provision", "/remove"]:
                return self.reply(404, {"error": "Unknown operation"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("Invalid request size")
                value = json.loads(self.rfile.read(length))
                profile = validate_request(value, profiles)
                with LOCK:
                    if self.path == "/remove":
                        remove(profile, value)
                        result = {"removed": True}
                    else:
                        result = provision(profile, value)
                self.reply(200, result)
            except ValueError:
                self.reply(400, {"error": "Invalid provisioning request"})
            except Exception:
                self.reply(503, {"error": "Provisioning failed"})

    path = Path(socket_path)
    if path.exists():
        path.unlink()
    with UnixServer(str(path), Handler) as server:
        os.chown(path, 0, 1000)
        path.chmod(0o660)
        server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--socket", required=True)
    args = parser.parse_args()
    serve(json.loads(Path(args.config).read_text()), args.socket)
