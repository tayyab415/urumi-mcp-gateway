"""Keep the browser tunnel attached and save its current private entry link."""

import json
import re
import signal
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
process = subprocess.Popen([
    "ssh", "-T",
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", f"UserKnownHostsFile={ROOT}/.runtime/tunnel-known-hosts",
    "-o", "IdentityFile=none",
    "-o", "IdentitiesOnly=yes",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3",
    "-o", "ConnectTimeout=10",
    "-R", "80:127.0.0.1:18771", "nokey@localhost.run",
    "--", "--output", "json",
], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def stop(_signal, _frame):
    process.terminate()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
for line in process.stdout:
    try:
        item = json.loads(line)
    except ValueError:
        continue
    if item.get("type") == "v1.tcpip_forward.register.accepted":
        match = re.search(r"https://[a-zA-Z0-9.-]+", item.get("data", ""))
        if match:
            url = match[0]
            key = (ROOT / ".runtime/console-tunnel-key").read_text().strip()
            link = ROOT / ".runtime/console-tunnel-link"
            link.touch(mode=0o600, exist_ok=True)
            link.chmod(0o600)
            link.write_text(f"{url}/open?key={key}\n")
            print(f"Console HTTPS tunnel connected: {url}", flush=True)
raise SystemExit(process.wait())
