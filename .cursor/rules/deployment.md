# Deployment Rules for Personal Assistant CN

## Critical: Python Bytecode Cache (__pycache__)

This project runs in Docker with a volume mount (`./app:/code/app`).
Python's `__pycache__` directories persist on the host filesystem across
container restarts. **Stale `.pyc` files have caused repeated production
outages** where the server returns 500 errors with `NameError` for symbols
that exist in the source `.py` file.

### Protections in place (DO NOT remove):

1. **`PYTHONDONTWRITEBYTECODE=1`** — Set in `Dockerfile`, `docker-compose.yml`,
   and the server's `.env`. Prevents Python from creating `.pyc` files.

2. **`_verify_critical_imports()`** in `app/main.py` — Runs at import time
   before the FastAPI app starts. If any critical symbol is missing, the
   process exits immediately with a clear error instead of silently serving
   500s on every request.

3. **`scripts/deploy_sync.py`** clears all `__pycache__` directories inside the
   container before copying new code, and runs a post-deploy health check
   that verifies `/health` returns 200 and `/api/skills/stock_alert/dashboard/detail`
   returns 200. If verification fails, the script exits with error code 1.

## Critical: Python Import Binding for memory_store

The `memory_store` singleton is initialized as `MemoryStore()` (in-memory)
at module load time, then replaced with `PersistentMemoryStore` during
FastAPI's `lifespan`. Python's `from X import Y` creates a **name binding**
that does NOT update when the module attribute changes.

**DO NOT** use `from app.engine.memory import memory_store` at the top
level of any skill or module that needs persistent storage. Use:

```python
import app.engine.memory as _mem_module
# Then access: _mem_module.memory_store.get_skill(...)
```

This ensures you always get the current (persistent) store, regardless of
import order.

### Rules for any code changes:

- **NEVER** use `sed`, `echo >>`, or similar shell commands to modify `.py`
  files on the server. This has caused syntax errors that were masked by
  stale `.pyc` files, leading to hours of debugging.

- **ALWAYS** deploy via `python scripts/deploy_sync.py` (fast) or `python scripts/deploy_rebuild.py` (full rebuild). Never manually `docker cp`
  individual files.

- If adding a new import to `app/api/skills.py` or any module used by the
  dashboard/scheduler, add the symbol to `_verify_critical_imports()` in
  `app/main.py` so startup fails fast if it's missing.

- If the bot stops responding or the dashboard returns 500 errors, the
  first thing to check is `docker logs assistant_app --tail 30` for
  `NameError` or `ImportError`.

## Server Details

- **Host**: 43.143.114.183
- **User**: ubuntu
- **Container**: assistant_app
- **App path (container)**: /code/app/
- **App path (host volume)**: /opt/assistant/app/
- **Timezone**: CST (UTC+8)
