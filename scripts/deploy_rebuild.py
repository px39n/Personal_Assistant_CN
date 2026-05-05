"""Full remote deploy: tarball (app + compose files) → upload → `docker compose up -d --build`.

Use when Dockerfile, dependencies, or compose layout changed — slower than deploy_sync.

Usage (from repo root):
  python scripts/deploy_rebuild.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import paramiko

from deploy_credentials import get_ssh_connect_kwargs
REMOTE_DIR = "/opt/assistant"
BUNDLE = "deploy_bundle.tar.gz"

# Patterns for tar --exclude (omit dev scripts with credentials from the bundle)
EXCLUDES = [
    "__pycache__",
    "*.pyc",
    ".env",
    "node_modules",
    "web",
    "tests",
    "*.png",
    "scripts",
    "test_*.py",
    "SKILLS_UI_TEST_REPORT.md",
    ".git",
    BUNDLE,
    ".agent",
    ".cursor",
]


def main() -> None:
    print("[1/4] Packing...")
    exc = " ".join(f"--exclude='{e}'" for e in EXCLUDES)
    # Windows tar; suppress stderr noise
    os.system(f"tar -czf {BUNDLE} {exc} app Dockerfile docker-compose.yml pyproject.toml .env.production README.md 2>nul")

    print("[2/4] Uploading...")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(timeout=10, **get_ssh_connect_kwargs())
    sftp = c.open_sftp()
    sftp.put(BUNDLE, f"/tmp/{BUNDLE}")
    sftp.close()

    print("[3/4] Extracting + rebuilding...")
    cmd = (
        f"sudo tar -xzf /tmp/{BUNDLE} -C {REMOTE_DIR} && "
        f"cd {REMOTE_DIR} && "
        f"sudo docker compose up -d --build 2>&1 | tail -5"
    )
    _, stdout, stderr = c.exec_command(cmd, timeout=300)
    print(stdout.read().decode())
    print(stderr.read().decode())

    print("[4/4] Health check...")
    _, stdout, _ = c.exec_command("curl -s http://localhost:8000/health", timeout=15)
    print(stdout.read().decode())

    c.close()
    try:
        os.remove(BUNDLE)
    except OSError:
        pass
    print("\nDone!")


if __name__ == "__main__":
    main()
