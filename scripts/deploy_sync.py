"""Fast deploy: syntax-check → bundle app/ → SFTP → docker cp + restart (no image rebuild).

Prefer this for day-to-day Python/API/static changes under app/.

Usage (from repo root):
  python scripts/deploy_sync.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import paramiko

from deploy_credentials import get_ssh_connect_kwargs

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
CONTAINER = "assistant_app"
HEALTH_URL = "http://127.0.0.1:8000/health"
DETAIL_URL = (
    "http://127.0.0.1:8000/api/skills/stock_alert/dashboard/detail"
    "?code=600487.SH&span=3m"
)


# ── 1. Pre-deploy: syntax check all .py ──
print("=== 1/5 Pre-deploy syntax check ===")
errors = []
for root, dirs, files in os.walk("app"):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for f in files:
        if f.endswith(".py"):
            path = os.path.join(root, f)
            r = subprocess.run(
                [sys.executable, "-m", "py_compile", path],
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                errors.append(f"{path}: {r.stderr.strip()}")
if errors:
    print(f"SYNTAX ERRORS ({len(errors)}):")
    for e in errors:
        print(f"  {e}")
    sys.exit(1)
print("All .py files OK")


# ── 2. Bundle ──
print("\n=== 2/5 Bundle ===")
bundle_path = ROOT / "deploy_bundle.tar.gz"
with tarfile.open(bundle_path, "w:gz") as tar:
    tar.add("app", arcname="app")
print(f"Bundle: {bundle_path.stat().st_size} bytes")


# ── 3. Upload ──
print("\n=== 3/5 Upload ===")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(timeout=10, **get_ssh_connect_kwargs())
sftp = c.open_sftp()
sftp.put(str(bundle_path), "/tmp/deploy_bundle.tar.gz")
sftp.close()
print("Uploaded")


# ── 4. Deploy: stop → clear cache → copy → start ──
print("\n=== 4/5 Deploy ===")


def _ssh(cmd: str, timeout: int = 60):
    _, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    stderr.read().decode("utf-8", errors="replace").strip()
    return out


steps = [
    ("Stop container", f"sudo docker stop {CONTAINER}"),
    (
        "Clear __pycache__",
        f"sudo docker start {CONTAINER} && sleep 1"
        f" && sudo docker exec {CONTAINER} find /code/app -type d -name __pycache__ -exec rm -rf {{}} + 2>/dev/null; true"
        f" && sudo docker stop {CONTAINER}",
    ),
    (
        "Unpack & copy new code",
        "sudo rm -rf /tmp/app && cd /tmp && tar xzf deploy_bundle.tar.gz"
        f" && sudo docker cp app/. {CONTAINER}:/code/app/",
    ),
    (
        "Set PYTHONDONTWRITEBYTECODE",
        f"sudo docker start {CONTAINER} && sleep 1"
        f" && sudo docker exec {CONTAINER} sh -c 'echo PYTHONDONTWRITEBYTECODE=1 >> /etc/environment' 2>/dev/null; true"
        f" && sudo docker stop {CONTAINER}",
    ),
    ("Start container", f"sudo docker start {CONTAINER}"),
]
for label, cmd in steps:
    print(f"  {label}...")
    out = _ssh(cmd)
    if out:
        print(f"    {out}")


# ── 5. Post-deploy verification ──
print("\n=== 5/5 Post-deploy verification ===")
print("  Waiting 12s for Uvicorn startup...")
time.sleep(12)

passed = True

out = _ssh(f"curl -sf {HEALTH_URL}")
try:
    h = json.loads(out)
    mem = h.get("memory_store", "?")
    status = h.get("status", "?")
    if status == "ok" and mem == "PersistentMemoryStore":
        print(
            f"  [PASS] /health → status={status} memory={mem} "
            f"skills={h.get('skills_loaded')}"
        )
    else:
        print(f"  [FAIL] /health → status={status} memory={mem}")
        passed = False
except Exception:
    print(f"  [FAIL] /health → {out[:200]}")
    passed = False

STOCKS_URL = "http://127.0.0.1:8000/api/skills/stock_alert/dashboard/stocks"
out = _ssh(f"curl -sf '{STOCKS_URL}'")
try:
    d = json.loads(out)
    n_stocks = len(d.get("stocks", []))
    n_cfg = len(d.get("config", {}))
    if n_stocks > 0 and n_cfg > 0:
        print(f"  [PASS] /dashboard/stocks → {n_stocks} stocks, {n_cfg} config keys")
    else:
        print(
            f"  [WARN] /dashboard/stocks → {n_stocks} stocks, {n_cfg} config keys "
            "(may be empty watchlist)"
        )
except Exception:
    print(f"  [FAIL] /dashboard/stocks → {out[:200]}")
    passed = False

out = _ssh(f"curl -sf -o /dev/null -w '%{{http_code}}' '{DETAIL_URL}'")
if out.strip("'") == "200":
    print("  [PASS] /dashboard/detail → 200")
else:
    print(f"  [FAIL] /dashboard/detail → {out}")
    passed = False

out = _ssh(
    f"sudo docker logs {CONTAINER} --tail 30 2>&1 | "
    "grep -iE 'FATAL|SystemExit|NameError|ImportError|critical import'"
)
if out:
    print(f"  [FAIL] Startup errors: {out[:300]}")
    passed = False
else:
    print("  [PASS] No startup errors")

c.close()

if passed:
    print("\n=== Deploy SUCCESS ===")
else:
    print("\n=== Deploy FAILED — check errors above ===")
    sys.exit(1)
