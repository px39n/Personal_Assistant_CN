"""SSH helper: run a shell command on the remote host (ops / debugging).

Usage:
  python scripts/remote_shell.py
  python scripts/remote_shell.py "docker logs assistant_app --tail 40"
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import paramiko

from deploy_credentials import get_ssh_connect_kwargs


def ssh_run(cmd: str, timeout: int = 600) -> str:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(timeout=10, **get_ssh_connect_kwargs())
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode()
    err = stderr.read().decode()
    client.close()
    return out + err


if __name__ == "__main__":
    cmd = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "echo connected && hostname && docker --version"
    print(ssh_run(cmd))
