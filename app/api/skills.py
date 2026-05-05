"""技能管理 API — 列表、详情、配置、开关、用户数据。"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from loguru import logger

from app.config import DEFAULT_USER_ID
from app.skills.registry import skill_registry

router = APIRouter(prefix="/api/skills", tags=["skills"])

NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}


class SkillConfigUpdate(BaseModel):
    config: dict[str, Any]


# ── 列表 ──────────────────────────────────

@router.get("/")
async def list_skills(category: str | None = None):
    """列出所有技能（可按分类筛选）"""
    skills = skill_registry.list_all()
    if category:
        skills = [s for s in skills if s.category.value == category]

    from app.skills.base import CATEGORY_META
    categories = []
    seen = set()
    for s in skill_registry.list_all():
        cat = s.category.value
        if cat not in seen:
            seen.add(cat)
            meta = CATEGORY_META.get(cat, {})
            categories.append({
                "value": cat,
                "label": meta.get("label", cat),
                "color": meta.get("color", "#888"),
            })

    return JSONResponse(
        content={
            "count": len(skills),
            "categories": categories,
            "skills": [s.get_summary() for s in skills],
        },
        headers=NO_CACHE,
    )


# ── Router 信息 ──────────────────────────────

@router.get("/router/info")
async def router_info():
    """返回 Router 配置 + 所有 Skill 的 tool definitions"""
    from app.engine.router import ROUTER_SYSTEM_PROMPT, _build_dynamic_context
    from app.skills.base import CATEGORY_META

    dynamic_ctx = _build_dynamic_context()
    full_prompt = ROUTER_SYSTEM_PROMPT.format(dynamic_context=dynamic_ctx)

    tools = []
    for s in skill_registry.list_all():
        if not s.enabled:
            continue
        cat_meta = CATEGORY_META.get(s.category.value, {})
        params = s.parameters_schema or {}
        props = params.get("properties", {})
        required = params.get("required", [])
        tools.append({
            "name": s.name,
            "icon": s.icon,
            "description": s.description,
            "category": s.category.value,
            "category_label": cat_meta.get("label", s.category.value),
            "category_color": cat_meta.get("color", "#888"),
            "parameters": [
                {
                    "name": k,
                    "type": v.get("type", "string"),
                    "description": v.get("description", ""),
                    "required": k in required,
                    "enum": v.get("enum"),
                    "default": v.get("default"),
                }
                for k, v in props.items()
            ],
        })

    rules = []
    for line in full_prompt.split("\n"):
        line = line.strip()
        if line.startswith("- ") and "→" in line:
            parts = line[2:].split("→", 1)
            rules.append({
                "trigger": parts[0].strip(),
                "target": parts[1].strip().replace("必须调用 ", ""),
            })

    return JSONResponse(content={
        "system_prompt": full_prompt,
        "routing_rules": rules,
        "tools": tools,
        "tool_count": len(tools),
    }, headers=NO_CACHE)


# ── 详情 ──────────────────────────────────

@router.get("/{name}")
async def get_skill_detail(name: str):
    """获取单个技能的完整详情（含配置）"""
    s = skill_registry.get(name)
    if not s:
        raise HTTPException(404, f"技能 '{name}' 不存在")
    return JSONResponse(content=s.get_detail(), headers=NO_CACHE)


# ── 配置 ──────────────────────────────────

@router.put("/skill/{name}/config")
async def update_skill_config(name: str, body: SkillConfigUpdate):
    """更新技能配置（通用）"""
    s = skill_registry.get(name)
    if not s:
        raise HTTPException(404, f"技能 '{name}' 不存在")
    if not s.config_schema.get("properties"):
        raise HTTPException(400, f"技能 '{name}' 没有可配置项")
    updated = s.update_config(body.config)
    return {"success": True, "config": updated}


# ── 开关 ──────────────────────────────────

@router.post("/{name}/toggle")
async def toggle_skill(name: str):
    """启用/禁用技能"""
    s = skill_registry.get(name)
    if not s:
        raise HTTPException(404, f"技能 '{name}' 不存在")
    s.enabled = not s.enabled
    return {"success": True, "name": name, "enabled": s.enabled}


# ── 用户数据 ──────────────────────────────

@router.get("/{name}/user_data")
async def get_skill_user_data(name: str):
    """获取当前用户的 Skill 记忆数据（单用户模式）"""
    s = skill_registry.get(name)
    if not s:
        raise HTTPException(404, f"技能 '{name}' 不存在")

    from app.engine.memory import memory_store
    data = await memory_store.get_all_skill(DEFAULT_USER_ID, name)
    return JSONResponse(
        content={"skill": name, "user_id": DEFAULT_USER_ID, "data": data},
        headers=NO_CACHE,
    )


class UserDataUpdate(BaseModel):
    key: str
    value: Any


@router.put("/{name}/user_data")
async def update_skill_user_data(name: str, body: UserDataUpdate):
    """更新用户的 Skill 记忆数据（单用户模式）"""
    s = skill_registry.get(name)
    if not s:
        raise HTTPException(404, f"技能 '{name}' 不存在")

    from app.engine.memory import memory_store
    await memory_store.set_skill(DEFAULT_USER_ID, name, body.key, body.value)
    return {"success": True, "skill": name, "key": body.key, "value": body.value}


# ── 推送目标（已知飞书群）──────────────────

@router.get("/push_targets/feishu_groups")
async def get_feishu_groups():
    """获取已知的飞书群列表（机器人曾在其中收到过消息的群）"""
    from app.engine.memory import memory_store
    groups = await memory_store.get_global(DEFAULT_USER_ID, "feishu_groups", []) or []
    return JSONResponse(content={"groups": groups}, headers=NO_CACHE)


# ── 推送调度状态 ──────────────────────────

@router.get("/portfolio/push_status")
async def get_push_status():
    """获取推送调度状态：时间线、下次推送、上次推送等"""
    from app.engine.scheduler import get_push_status
    status = await get_push_status()
    return JSONResponse(content=status, headers=NO_CACHE)


@router.post("/portfolio/push_now")
async def push_now():
    """立即执行一次推送"""
    from app.engine.scheduler import execute_push_now
    result = await execute_push_now()
    return result


# ── 主力资金预警 ──────────────────────────

@router.post("/stock_alert/backtest")
async def stock_alert_backtest(request: Request):
    """执行主力资金预警回测。"""
    from app.skills.finance.stock_alert import (
        _run_backtest, _format_alert, render_push_chart,
        get_watchlist, get_alert_config, get_alert_snapshots,
    )
    from app.data import market_data
    import asyncio

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    days = body.get("days", 7)
    with_charts = body.get("with_charts", True)

    watchlist = await get_watchlist()
    if not watchlist:
        return JSONResponse(content={"success": True, "alerts": [], "stats": {}, "source": "none"}, headers=NO_CACHE)

    config = await get_alert_config()
    loop = asyncio.get_event_loop()

    snapshots = await get_alert_snapshots(days)
    use_snapshots = len(snapshots) > 0
    source = "intraday_snapshot" if use_snapshots else "daily_fallback"

    if use_snapshots:
        raw_alerts = snapshots
    else:
        try:
            result = await loop.run_in_executor(None, _run_backtest, watchlist, config, days)
        except Exception as e:
            return JSONResponse(content={"success": False, "error": str(e)}, headers=NO_CACHE)
        raw_alerts = result.get("alerts", [])

    raw_alerts.sort(key=lambda a: a.get("datetime", a.get("date", "")), reverse=True)

    type_counts: dict[str, int] = {}
    alerts_out = []
    for a in raw_alerts:
        t = a.get("type", "")
        type_counts[t] = type_counts.get(t, 0) + 1

        date_str = a.get("date", "")
        time_str = a.get("time", "09:25")
        dt_str = a.get("datetime", f"{date_str} {time_str}")

        entry = {
            "date": date_str,
            "time": time_str,
            "datetime": dt_str,
            "code": a.get("code", ""),
            "ts_code": a.get("ts_code", ""),
            "name": a.get("name", ""),
            "type": t,
            "level": a.get("level", ""),
            "net_pct": a.get("net_pct", 0),
            "net_amount": a.get("net_amount", 0),
            "price": a.get("price", 0),
            "pct_chg": a.get("pct_chg", 0),
            "message": _format_alert(a),
            "chart": "",
        }

        if with_charts:
            try:
                ts_code = a.get("ts_code", "")
                if not ts_code or "." not in ts_code:
                    ts_code = a.get("code", "") + (".SH" if a.get("code", "").startswith("6") else ".SZ")
                price_hist = await loop.run_in_executor(None, market_data.get_price, ts_code, 14)
                dp_hist = await loop.run_in_executor(None, market_data.get_main_force, ts_code, 14)
                b64 = await loop.run_in_executor(None, render_push_chart, a, price_hist, dp_hist)
                entry["chart"] = b64
            except Exception:
                pass

        alerts_out.append(entry)

    stocks_set = set(a.get("ts_code") or a.get("code") for a in raw_alerts)

    return JSONResponse(
        content={
            "success": True,
            "alerts": alerts_out,
            "source": source,
            "stats": {
                "total": len(raw_alerts),
                "dp": type_counts.get("double_purple", 0),
                "inflow": type_counts.get("inflow", 0),
                "outflow": type_counts.get("outflow", 0),
                "trend": type_counts.get("trend_in", 0) + type_counts.get("trend_out", 0),
                "reversal": type_counts.get("reversal_inflow", 0) + type_counts.get("reversal_outflow", 0),
                "days": days,
                "stocks": len(stocks_set),
            },
        },
        headers=NO_CACHE,
    )


@router.post("/stock_alert/check_now")
async def stock_alert_check_now():
    """立即执行一次主力资金预警检测"""
    from app.skills.finance.stock_alert import StockAlertSkill

    s = StockAlertSkill()
    result = await s._check(DEFAULT_USER_ID)
    return JSONResponse(
        content={
            "success": result.success,
            "summary": result.summary,
            "error": result.error,
        },
        headers=NO_CACHE,
    )


@router.post("/stock_alert/market_scan")
async def stock_alert_market_scan():
    """手动触发全市场双紫扫描"""
    import asyncio
    from app.skills.finance.stock_alert import scan_market_dp, get_alert_config

    config = await get_alert_config()
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, scan_market_dp, config)
    except Exception as e:
        return JSONResponse(
            content={"success": False, "error": str(e)},
            headers=NO_CACHE,
        )

    return JSONResponse(
        content={
            "success": True,
            "trade_date": result.get("trade_date", ""),
            "total_scanned": result.get("total_scanned", 0),
            "hits": result.get("hits", []),
        },
        headers=NO_CACHE,
    )


# ── Dashboard 数据 API ──────────────────────

@router.get("/stock_alert/dashboard/stocks")
async def stock_alert_dashboard_stocks():
    """Dashboard: 股票列表 + 今日资金流摘要 + 双紫实时状态。

    所有数据通过 market_data 统一接口获取。
    """
    import asyncio, time
    from app.skills.finance.stock_alert import (
        get_watchlist, get_alert_config, _rolling_cache,
        _safe_float, _analyze_trend_records,
    )
    from app.data import market_data

    watchlist = await get_watchlist()
    if not watchlist:
        return JSONResponse(content={"stocks": [], "config": {}}, headers=NO_CACHE)

    config = await get_alert_config()

    wl_hash = ",".join(w["code"] for w in watchlist)
    _stocks_key = f"stocks_resp:{wl_hash}"
    _cached = _rolling_cache.get(_stocks_key)
    if _cached and time.time() - _cached.get("ts", 0) < 300:
        _cached["data"]["config"] = config
        from app.config import settings
        _cached["data"]["config"]["em_proxy_url"] = bool(settings.em_proxy_url)
        return JSONResponse(content=_cached["data"], headers=NO_CACHE)

    loop = asyncio.get_event_loop()
    stocks = []
    dp_enabled = config.get("dp_enabled", True)

    for item in watchlist:
        code = item["code"].split(".")[0]
        entry: dict = {"code": item["code"], "code6": code, "name": item["name"]}

        try:
            flow_records = await loop.run_in_executor(
                None, market_data.get_fund_flow, item["code"], 8,
            )
            if flow_records:
                today = flow_records[-1]
                entry["net_pct"] = _safe_float(today.get("net_pct"))
                entry["net_amount"] = _safe_float(today.get("net_amount"))
                entry["price"] = _safe_float(today.get("close"))
                entry["pct_chg"] = _safe_float(today.get("pct_chg"))
                if len(flow_records) >= 2:
                    entry["trend"] = _analyze_trend_records(flow_records)
        except Exception:
            pass

        try:
            price_snap = await loop.run_in_executor(
                None, market_data.get_price, item["code"], 0,
            )
            if price_snap:
                p = price_snap[0]
                entry["price"] = _safe_float(p.get("price", p.get("close", 0)))
                entry["pct_chg"] = _safe_float(p.get("pct_chg", 0))
        except Exception:
            pass

        if dp_enabled:
            dp_cached = _rolling_cache.get(f"dp_today:{item['code']}")
            if dp_cached:
                entry["dp"] = dp_cached["data"]

        stocks.append(entry)

    resp_data = {"stocks": stocks, "config": config}
    _rolling_cache[_stocks_key] = {"data": resp_data, "ts": time.time()}

    from app.config import settings
    config["em_proxy_url"] = bool(settings.em_proxy_url)
    return JSONResponse(content=resp_data, headers=NO_CACHE)


_KLINE_SPAN = {"1m": 40, "3m": 120, "6m": 200, "1y": 400}
_KLINE_FLOW_DAYS = {"1m": 22, "3m": 60, "6m": 120, "1y": 250}


@router.get("/stock_alert/dashboard/detail")
async def stock_alert_dashboard_detail(
    code: str,
    span: str = "3m",
):
    """Dashboard: 单只股票详情 — 历史 + 今日实时，全部通过 market_data 统一获取。"""
    import asyncio
    from app.skills.finance.stock_alert import (
        get_dp_today_slot, get_alert_config as _get_cfg,
        compute_zhuli_status, _rolling_cache,
    )
    from app.data import market_data

    loop = asyncio.get_event_loop()
    flow_tail = _KLINE_FLOW_DAYS.get(span, 30)
    dp_days_map = {"1m": 30, "3m": 90, "6m": 180, "1y": 365}
    status_days = dp_days_map.get(span, 90)

    flow_fut = loop.run_in_executor(None, market_data.get_fund_flow, code, flow_tail)
    dp_fut = loop.run_in_executor(None, market_data.get_main_force, code, dp_days_map.get(span, 90))
    status_fut = loop.run_in_executor(None, compute_zhuli_status, code, status_days)

    cfg = await _get_cfg()
    dp_today_fut = loop.run_in_executor(None, get_dp_today_slot, code, cfg)

    fund_flow: list[dict] = []
    dp_history: list[dict] = []
    dp_today: dict = {}
    zhuli_status: list = []
    data_source = "none"
    trend: dict = {}

    try:
        fund_flow = await flow_fut
    except Exception:
        pass
    try:
        dp_history = await dp_fut
    except Exception:
        pass
    try:
        dp_today, data_source = await dp_today_fut
    except Exception:
        pass
    try:
        zhuli_status = await status_fut
        if zhuli_status:
            _rolling_cache[f"zhuli_status:{code}"] = zhuli_status
    except Exception as e:
        logger.warning(f"[Dashboard] 主力状态计算失败: {e}")

    if fund_flow and len(fund_flow) >= 2:
        from app.skills.finance.stock_alert import _analyze_trend_records
        trend = _analyze_trend_records(fund_flow)

    from app.config import settings
    return JSONResponse(
        content={
            "code": code, "span": span,
            "fund_flow": fund_flow, "trend": trend,
            "dp_history": dp_history, "dp_today": dp_today,
            "zhuli_status": zhuli_status,
            "data_source": data_source,
            "em_proxy_url": bool(settings.em_proxy_url),
        },
        headers=NO_CACHE,
    )


class PushTargetUpdate(BaseModel):
    target: str


@router.put("/stock_alert/push_target")
async def stock_alert_set_push_target(body: PushTargetUpdate):
    """设置预警推送目标"""
    from app.engine.memory import memory_store
    from app.skills.finance.stock_alert import get_alert_config, ALERT_CONFIG_KEY

    config = await get_alert_config()
    config["push_target"] = body.target
    await memory_store.set_skill(DEFAULT_USER_ID, "stock_alert", ALERT_CONFIG_KEY, config)
    return {"success": True, "target": body.target}


@router.put("/stock_alert/config")
async def stock_alert_save_config(body: SkillConfigUpdate):
    """保存预警完整配置到持久存储"""
    from app.engine.memory import memory_store
    from app.skills.finance.stock_alert import get_alert_config, ALERT_CONFIG_KEY, _DEFAULTS
    config = await get_alert_config()
    for k, v in body.config.items():
        if k in _DEFAULTS:
            config[k] = v
    await memory_store.set_skill(DEFAULT_USER_ID, "stock_alert", ALERT_CONFIG_KEY, config)
    return {"success": True, "config": config}


@router.get("/stock_alert/dashboard/search")
async def stock_alert_search_stock(q: str):
    """模糊搜索股票（名称或代码），返回最多 8 条"""
    import asyncio
    from app.skills.finance.stock_chart import _load_stock_map, _name_cache, _code_cache

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _load_stock_map)

    s = q.strip().lower()
    if not s:
        return {"results": []}

    matches: list[dict] = []
    clean = s.replace(".", "").replace("sz", "").replace("sh", "")

    for name, code in _code_cache.items():
        code6 = code.split(".")[0]
        if s == name or s == code6:
            matches.insert(0, {"code": code, "name": name})
        elif s in name.lower() or (clean.isdigit() and clean in code6):
            matches.append({"code": code, "name": name})
        if len(matches) >= 8:
            break

    return {"results": matches}


class WatchlistAction(BaseModel):
    code: str
    name: str


@router.post("/stock_alert/dashboard/add_stock")
async def stock_alert_add_stock(body: WatchlistAction):
    """添加股票到持仓列表"""
    from app.engine.memory import memory_store

    wl = await memory_store.get_skill(DEFAULT_USER_ID, "portfolio", "watchlist") or []
    if any(item["code"] == body.code for item in wl):
        return {"success": False, "error": f"{body.name} 已在列表中"}
    wl.append({"code": body.code, "name": body.name})
    await memory_store.set_skill(DEFAULT_USER_ID, "portfolio", "watchlist", wl)
    return {"success": True, "name": body.name, "count": len(wl)}


@router.post("/stock_alert/dashboard/remove_stock")
async def stock_alert_remove_stock(body: WatchlistAction):
    """从持仓列表移除股票"""
    from app.engine.memory import memory_store

    wl = await memory_store.get_skill(DEFAULT_USER_ID, "portfolio", "watchlist") or []
    before = len(wl)
    wl = [item for item in wl if item["code"] != body.code]
    if len(wl) == before:
        return {"success": False, "error": "未找到该股票"}
    await memory_store.set_skill(DEFAULT_USER_ID, "portfolio", "watchlist", wl)
    return {"success": True, "name": body.name, "count": len(wl)}


@router.post("/stock_alert/test_push")
async def stock_alert_test_push(request: Request):
    """测试推送 — 生成模拟预警并推送到飞书 + 返回到前端预览。

    数据全部通过 market_data 统一接口获取（自动含今日实时）。
    """
    import asyncio
    from app.skills.finance.stock_alert import (
        get_watchlist, get_alert_config, render_push_chart,
        _format_alert, _safe_float,
    )
    from app.data import market_data

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    alert_type = body.get("alert_type", "inflow")
    code = body.get("code", "")

    watchlist = await get_watchlist()
    if not code and watchlist:
        code = watchlist[0]["code"]
    if not code:
        return JSONResponse(content={"success": False, "error": "无持仓股票"}, status_code=400)

    stock_name = code
    for w in watchlist:
        if w["code"] == code:
            stock_name = w.get("name", code)
            break

    ts_code = code
    if "." not in ts_code:
        ts_code = code + (".SH" if code.startswith("6") else ".SZ")

    config = await get_alert_config()

    mock_alert = {
        "type": alert_type,
        "code": code.replace(".SH", "").replace(".SZ", ""),
        "ts_code": ts_code,
        "name": stock_name,
        "price": 0,
        "pct_chg": 0,
        "net_amount": 0,
        "net_pct": 0,
        "level": "red" if "inflow" in alert_type or alert_type == "double_purple" else "green",
        "trend": {},
        "dp": {},
        "date": "",
        "time": "",
    }

    loop = asyncio.get_event_loop()

    price_snap = await loop.run_in_executor(None, market_data.get_price, ts_code, 0)
    if price_snap:
        p = price_snap[0]
        mock_alert["price"] = _safe_float(p.get("price", p.get("close", 0)))
        mock_alert["pct_chg"] = _safe_float(p.get("pct_chg", 0))

    flow_today = await loop.run_in_executor(None, market_data.get_fund_flow, ts_code, 0)
    if flow_today:
        f = flow_today[0]
        mock_alert["net_amount"] = _safe_float(f.get("net_amount"))
        mock_alert["net_pct"] = _safe_float(f.get("net_pct"))

    price_hist = await loop.run_in_executor(None, market_data.get_price, ts_code, 14)
    dp_hist = await loop.run_in_executor(None, market_data.get_main_force, ts_code, 30)

    chart_b64 = await loop.run_in_executor(
        None, render_push_chart, mock_alert, price_hist, dp_hist, 14
    )

    text_msg = _format_alert(mock_alert)

    push_result = {"sent": False, "error": ""}
    try:
        from app.engine.scheduler import _send_push
        full_msg = f"🧪 测试推送 — {stock_name}\n{text_msg}"
        push_target = body.get("target") or config.get("push_target_alert", "private")
        await _send_push(
            full_msg,
            with_buttons=False,
            target_override=push_target if push_target != "private" else None,
            image_b64_list=[chart_b64] if chart_b64 else None,
        )
        push_result["sent"] = True
    except Exception as e:
        push_result["error"] = str(e)
        logger.warning(f"[测试推送] 飞书发送失败: {e}")

    return JSONResponse(content={
        "success": True,
        "alert_type": alert_type,
        "code": code,
        "name": stock_name,
        "text": text_msg,
        "chart_b64": chart_b64,
        "push_result": push_result,
    }, headers=NO_CACHE)


@router.post("/stock_alert/test_push_portfolio")
async def stock_alert_test_push_portfolio(request: Request):
    """测试每日持仓推送 — 用实时数据生成持仓快报并推送到飞书。"""
    from app.skills.finance.portfolio import PortfolioSkill, generate_commentary
    from app.skills.finance.stock_alert import get_alert_config

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    config = await get_alert_config()
    push_target = body.get("target") or config.get("push_target_portfolio") or config.get("push_target") or None

    data = None
    try:
        from app.engine.scheduler import _get_push_data
        data = await _get_push_data()
    except Exception as e:
        return JSONResponse(content={"success": False, "error": f"获取持仓数据失败: {e}"}, status_code=500)

    if not data or not data.get("watchlist"):
        return JSONResponse(content={"success": False, "error": "持仓列表为空"}, status_code=400)

    skill = PortfolioSkill()
    report = skill._fetch_report(data["watchlist"])
    if not report.success or not report.summary:
        err = report.summary or "生成报告失败（实时行情接口可能暂时不可用，请稍后重试）"
        return JSONResponse(content={"success": False, "error": err}, status_code=500)

    commentary = await generate_commentary(
        data["watchlist"], report.summary,
        report.data.get("current_prices", {}), is_open=True,
    )
    msg = f"🧪 测试 — 📊 开盘快报\n\n{report.summary}"
    if commentary:
        msg += f"\n\n📝 播报\n{commentary}"

    push_result = {"sent": False, "error": ""}
    try:
        from app.engine.scheduler import _send_push
        await _send_push(msg, with_buttons=False,
                         target_override=push_target if push_target != "private" else None)
        push_result["sent"] = True
    except Exception as e:
        push_result["error"] = str(e)

    return JSONResponse(content={
        "success": True, "text": msg, "push_result": push_result,
    }, headers=NO_CACHE)


@router.post("/stock_alert/test_push_scan")
async def stock_alert_test_push_scan(request: Request):
    """测试全盘扫描推送 — 实际执行扫描并推送结果。"""
    import asyncio
    from app.skills.finance.stock_alert import (
        scan_market_dp, format_scan_message, get_alert_config,
    )

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    config = await get_alert_config()
    push_target = body.get("target") or config.get("push_target_scan") or config.get("push_target") or None

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, scan_market_dp, config)
    except Exception as e:
        return JSONResponse(content={"success": False, "error": f"扫描失败: {e}"}, status_code=500)

    hits = result.get("hits", [])
    msg = format_scan_message(result)
    text = f"🧪 测试 — {msg}"

    push_result = {"sent": False, "error": ""}
    try:
        from app.engine.scheduler import _send_push
        await _send_push(text, with_buttons=False,
                         target_override=push_target if push_target != "private" else None)
        push_result["sent"] = True
    except Exception as e:
        push_result["error"] = str(e)

    return JSONResponse(content={
        "success": True, "text": text, "hits": len(hits),
        "push_result": push_result,
    }, headers=NO_CACHE)


@router.post("/stock_alert/dp_backtest")
async def stock_alert_dp_backtest(request: Request):
    """双紫信号历史回测 — 扫描过去N天信号并统计持有收益。"""
    import asyncio
    from app.skills.finance.stock_alert import backtest_dp_signal, get_alert_config

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    lookback = body.get("lookback_days", 60)
    hold = body.get("hold_days", 10)
    config = await get_alert_config()

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, backtest_dp_signal, lookback, hold, config)
    except Exception as e:
        return JSONResponse(content={"success": False, "error": f"回测失败: {e}"}, status_code=500)

    if "error" in result:
        return JSONResponse(content={"success": False, "error": result["error"]}, status_code=500)

    return JSONResponse(content={"success": True, **result}, headers=NO_CACHE)


# ╔══════════════════════════════════════════════════════════╗
# ║  聊天伴侣 (companion) API                               ║
# ╚══════════════════════════════════════════════════════════╝

@router.get("/companion/config")
async def companion_get_config():
    from app.skills.chat.companion import _get_config, _get_companion_group
    cfg = await _get_config()
    group = await _get_companion_group()
    return JSONResponse(content={"config": cfg, "companion_group": group}, headers=NO_CACHE)


@router.put("/companion/config")
async def companion_put_config(req: Request):
    body = await req.json()
    new_cfg = body.get("config", {})
    from app.skills.chat.companion import _get_config, _save_config
    cfg = await _get_config()
    cfg.update(new_cfg)
    await _save_config(cfg)
    return JSONResponse(content={"success": True, "config": cfg})


@router.get("/companion/memories")
async def companion_get_memories():
    from app.skills.chat.companion import _get_memories
    mems = await _get_memories()
    return JSONResponse(content={"memories": mems}, headers=NO_CACHE)


@router.post("/companion/memories")
async def companion_add_memory(req: Request):
    body = await req.json()
    content = body.get("content", "").strip()
    if not content:
        return JSONResponse(content={"success": False, "error": "内容不能为空"}, status_code=400)
    from app.skills.chat.companion import _get_memories, _save_memories
    from datetime import datetime
    mems = await _get_memories()
    mems.append({"content": content, "added": datetime.now().isoformat()})
    await _save_memories(mems)
    return JSONResponse(content={"success": True, "memories": mems})


@router.delete("/companion/memories/{idx}")
async def companion_delete_memory(idx: int):
    from app.skills.chat.companion import _get_memories, _save_memories
    mems = await _get_memories()
    if 0 <= idx < len(mems):
        removed = mems.pop(idx)
        await _save_memories(mems)
        return JSONResponse(content={"success": True, "removed": removed, "memories": mems})
    return JSONResponse(content={"success": False, "error": "索引越界"}, status_code=400)


@router.get("/companion/chat_log")
async def companion_get_chat_log():
    from app.skills.chat.companion import _get_chat_log
    log = await _get_chat_log()
    return JSONResponse(content={"chat_log": log[-50:]}, headers=NO_CACHE)


@router.delete("/companion/chat_log")
async def companion_clear_chat_log():
    from app.skills.chat.companion import _save_memories
    from app.engine import memory as _mem
    await _mem.memory_store.set_skill(DEFAULT_USER_ID, "companion", "chat_log", [])
    return JSONResponse(content={"success": True})


@router.put("/companion/group")
async def companion_set_group(req: Request):
    body = await req.json()
    chat_id = body.get("chat_id")
    from app.skills.chat.companion import _set_companion_group
    await _set_companion_group(chat_id if chat_id else None)
    return JSONResponse(content={"success": True, "companion_group": chat_id})


@router.post("/companion/test_chat")
async def companion_test_chat(req: Request):
    """Web 端测试聊天（不经过 Router）"""
    body = await req.json()
    message = body.get("message", "").strip()
    if not message:
        return JSONResponse(content={"success": False, "error": "消息不能为空"}, status_code=400)

    from app.skills.chat.companion import CompanionSkill, _get_config, _get_memories
    from app.skills.base import SkillContext

    ctx = SkillContext(user_id=DEFAULT_USER_ID)
    skill_instance = CompanionSkill()
    result = await skill_instance.execute(ctx, message=message)
    return JSONResponse(content={
        "success": result.success,
        "reply": result.summary if result.success else result.error,
    })


@router.get("/companion/wishes")
async def companion_get_wishes():
    from app.skills.chat.companion import _get_wishes
    wishes = await _get_wishes()
    return JSONResponse(content={"wishes": wishes}, headers=NO_CACHE)


@router.delete("/companion/wishes/{idx}")
async def companion_delete_wish(idx: int):
    from app.skills.chat.companion import _get_wishes, _save_wishes
    wishes = await _get_wishes()
    if 0 <= idx < len(wishes):
        removed = wishes.pop(idx)
        await _save_wishes(wishes)
        return JSONResponse(content={"success": True, "removed": removed})
    return JSONResponse(content={"success": False, "error": "索引越界"}, status_code=400)


@router.put("/companion/wishes/{idx}/status")
async def companion_update_wish_status(idx: int, req: Request):
    body = await req.json()
    new_status = body.get("status", "pending")
    from app.skills.chat.companion import _get_wishes, _save_wishes
    wishes = await _get_wishes()
    if 0 <= idx < len(wishes):
        wishes[idx]["status"] = new_status
        await _save_wishes(wishes)
        return JSONResponse(content={"success": True, "wish": wishes[idx]})
    return JSONResponse(content={"success": False, "error": "索引越界"}, status_code=400)


@router.post("/companion/wishes")
async def companion_add_wish_manual(req: Request):
    """手动添加功能许愿"""
    body = await req.json()
    desc = body.get("description", "").strip()
    if not desc:
        return JSONResponse(content={"success": False, "error": "描述不能为空"}, status_code=400)
    from app.skills.chat.companion import _add_wish
    await _add_wish(desc, "(手动添加)")
    from app.skills.chat.companion import _get_wishes
    wishes = await _get_wishes()
    return JSONResponse(content={"success": True, "wishes": wishes})
