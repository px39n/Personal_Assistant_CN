#!/usr/bin/env python3
"""Merge secrets from `.env` into `.env.production`, keep Docker-internal URLs."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / ".env"
PROD = ROOT / ".env.production"


def load_env(path: Path) -> dict[str, str]:
    d: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        d[k.strip()] = v.strip()
    return d


def main() -> None:
    if not DEV.is_file():
        raise SystemExit(f"Missing {DEV}")
    dev = load_env(DEV)
    lines: list[str] = [
        "# ============================================",
        "# 生产用 env：从本地 .env 同步密钥；勿提交（已被 .gitignore）",
        "# DATABASE_URL / REDIS / SEARXNG 为 Compose 内网",
        "# ============================================",
        "",
        "# LLM",
    ]
    for k in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_FAST_MODEL"):
        lines.append(f"{k}={dev.get(k, '')}")
    lines.extend(
        [
            "",
            "# 数据库（Docker Compose 内部网络）",
            "DATABASE_URL=postgresql+asyncpg://assistant:assistant@database:5432/assistant_cn",
            "",
            "# Redis",
            "REDIS_URL=redis://redis:6379/0",
            "",
            "# 搜索",
            "SEARXNG_URL=http://searxng:8080",
            "",
            "# 应用",
            f"APP_HOST={dev.get('APP_HOST', '0.0.0.0')}",
            f"APP_PORT={dev.get('APP_PORT', '8000')}",
            "APP_DEBUG=false",
            f"APP_SECRET_KEY={dev.get('APP_SECRET_KEY', '')}",
            "MEMORY_MODE=persistent",
            "",
            "# 金融数据",
        ]
    )
    for k in ("TUSHARE_TOKEN", "EM_PROXY_URL"):
        if dev.get(k):
            lines.append(f"{k}={dev[k]}")
    lines.extend(["", "# 企业微信"])
    for k in (
        "WECOM_CORP_ID",
        "WECOM_AGENT_ID",
        "WECOM_SECRET",
        "WECOM_TOKEN",
        "WECOM_ENCODING_AES_KEY",
    ):
        if dev.get(k):
            lines.append(f"{k}={dev[k]}")
    lines.extend(["", "# 飞书"])
    for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_VERIFICATION_TOKEN", "FEISHU_ENCRYPT_KEY"):
        lines.append(f"{k}={dev.get(k, '')}")

    PROD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {PROD} (no secrets printed)")


if __name__ == "__main__":
    main()
