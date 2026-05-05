"""FastAPI ???? ? ??????Skill ???????"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.memory import router as memory_router
from app.api.skills import router as skills_router
from app.api.wecom import router as wecom_router

try:
    from app.api.feishu import router as feishu_router
    _has_feishu = True
except ImportError:
    _has_feishu = False
    logger.warning("lark_oapi ???????????")
from app.config import DEFAULT_USER_ID, settings
from app.skills.registry import skill_registry


def _verify_critical_imports():
    """Fail-fast: verify key symbols are importable.

    Prevents the silent-500 scenario where a stale .pyc masks missing
    names and every API call returns an Internal Server Error.

    IMPORTANT: This must be called AFTER memory_store is replaced in
    lifespan, because stock_alert uses a top-level import binding.
    """
    checks = [
        ("app.api.skills", ["router", "logger"]),
        ("app.skills.finance.stock_alert", [
            "_rolling_cache", "compute_zhuli_status", "render_push_chart",
            "scan_market_dp",
        ]),
        ("app.engine.scheduler", ["start_scheduler", "stop_scheduler"]),
    ]
    for module_path, names in checks:
        try:
            mod = __import__(module_path, fromlist=names)
            for name in names:
                if not hasattr(mod, name):
                    raise ImportError(f"{module_path}.{name} not found")
        except Exception as e:
            logger.error(f"[STARTUP] Critical import failed: {e}")
            raise SystemExit(f"FATAL: {e} — clear __pycache__ and redeploy")


async def _migrate_to_default_user():
    """?? user_id ? skill_memory ????? default_user???????"""
    if settings.memory_mode != "persistent":
        return
    try:
        from app.engine.db import async_session_factory
        from app.models.memory import SkillMemory
        from sqlalchemy import select, distinct

        async with async_session_factory() as session:
            result = await session.execute(
                select(distinct(SkillMemory.user_id)).where(
                    SkillMemory.user_id != DEFAULT_USER_ID
                )
            )
            old_ids = [row[0] for row in result.all()]

        if not old_ids:
            return

        from app.engine.memory import memory_store
        for old_id in old_ids:
            old_data = await memory_store.get_all_skill(old_id, "portfolio")
            if not old_data:
                continue
            existing = await memory_store.get_all_skill(DEFAULT_USER_ID, "portfolio")
            for key, value in old_data.items():
                if key not in existing:
                    await memory_store.set_skill(DEFAULT_USER_ID, "portfolio", key, value)
                elif key == "watchlist" and isinstance(value, list) and isinstance(existing.get(key), list):
                    merged = list(existing[key])
                    existing_codes = {item["code"] for item in merged}
                    for item in value:
                        if item["code"] not in existing_codes:
                            merged.append(item)
                    await memory_store.set_skill(DEFAULT_USER_ID, "portfolio", key, merged)

            logger.info(f"??? {old_id} ???? {DEFAULT_USER_ID}")

    except Exception as e:
        logger.warning(f"???????????????: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """???????? ? ????? Skill???????"""
    logger.info("?? Personal Assistant CN...")

    # ???????
    from app.engine import memory as memory_module
    store = memory_module.create_memory_store(settings.memory_mode)
    memory_module.memory_store = store

    # ????????????
    if settings.memory_mode == "persistent":
        try:
            from app.engine.db import init_db
            await init_db()
            logger.info("?????????")
        except Exception as e:
            logger.warning(f"?????????????????: {e}")

    # Verify critical symbols before importing skills
    _verify_critical_imports()

    # ????????? Skill
    await skill_registry.auto_discover("app.skills")
    logger.info(f"??? {skill_registry.count} ? Skill")

    # ?? Channel
    from app.channels.base import channel_registry
    from app.channels.web import WebChannel

    channel_registry.register(WebChannel())

    # ?? Channel????? SDK ???????
    if _has_feishu and settings.feishu_app_id and settings.feishu_app_secret:
        try:
            from app.channels.feishu import FeishuChannel
            feishu_ch = FeishuChannel(
                app_id=settings.feishu_app_id,
                app_secret=settings.feishu_app_secret,
                verification_token=settings.feishu_verification_token,
                encrypt_key=settings.feishu_encrypt_key,
            )
            channel_registry.register(feishu_ch)
            logger.info("?? Channel ???")
        except Exception as e:
            logger.warning(f"?? Channel ????: {e}")

    if settings.wecom_corp_id and settings.wecom_secret:
        try:
            from app.channels.wecom import WeComChannel
            channel_registry.register(WeComChannel())
            logger.info("WeCom Channel ???")
        except Exception as e:
            logger.warning(f"WeCom Channel ????: {e}")

    await channel_registry.startup_all()
    logger.info(f"??? {channel_registry.count} ? Channel")

    # ????????? user_id ??? default_user
    await _migrate_to_default_user()

    from app.engine.scheduler import start_scheduler, stop_scheduler
    start_scheduler()

    yield

    stop_scheduler()
    logger.info("?? Personal Assistant CN...")
    await channel_registry.shutdown_all()
    for s in skill_registry.list_all():
        await s.on_unload()


app = FastAPI(
    title="Personal Assistant CN",
    description="????????? AI ????",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS ???
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ???????
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ????
app.include_router(chat_router)
app.include_router(skills_router)
app.include_router(documents_router)
app.include_router(memory_router)
app.include_router(wecom_router)
if _has_feishu:
    app.include_router(feishu_router)

# ??????? UI?
static_dir = Path(__file__).parent / "static"

from starlette.staticfiles import StaticFiles as _SF


class NoCacheStatic(_SF):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp


app.mount("/static", NoCacheStatic(directory=str(static_dir)), name="static")

dashboards_dir = static_dir / "dashboards"
dashboards_dir.mkdir(exist_ok=True)
app.mount("/dashboards", NoCacheStatic(directory=str(dashboards_dir)), name="dashboards")


@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")


@app.get("/health")
async def health_check():
    """????"""
    import app.engine.memory as _m
    from app.engine.scheduler import _is_trading_day, _is_trading_time

    mem_type = type(_m.memory_store).__name__
    checks = {
        "status": "ok" if mem_type != "MemoryStore" else "degraded",
        "skills_loaded": skill_registry.count,
        "memory_store": mem_type,
        "is_trading_day": _is_trading_day(),
        "is_trading_time": _is_trading_time(),
        "version": "0.1.0",
    }
    return checks


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_debug,
    )
