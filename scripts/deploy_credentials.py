"""Load DEPLOY_HOST / DEPLOY_USER / DEPLOY_PASSWORD for scripts (never commit values)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_dotenv_deploy() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = repo_root() / ".deploy.env"
    if env_path.is_file():
        load_dotenv(env_path)


def get_ssh_connect_kwargs() -> dict[str, str]:
    load_dotenv_deploy()
    host = os.environ.get("DEPLOY_HOST", "").strip()
    user = (os.environ.get("DEPLOY_USER", "ubuntu").strip() or "ubuntu")
    password = os.environ.get("DEPLOY_PASSWORD", "").strip()
    if not host or not password:
        print(
            "Missing DEPLOY_HOST or DEPLOY_PASSWORD.\n"
            "  Copy deploy/deploy.env.template → .deploy.env in the repo root, fill in values,\n"
            "  or export those variables in your environment.",
            file=sys.stderr,
        )
        sys.exit(1)
    return {"hostname": host, "username": user, "password": password}
