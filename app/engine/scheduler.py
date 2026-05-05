"""定时任务 — 持仓快报按用户配置的频率推送。

单用户模式: 数据统一存在 DEFAULT_USER_ID 下，
推送渠道路由从 global memory 的 feishu_routing 读取。

推送策略 (portfolio.push_config.frequency):
- open_only: 仅 9:25 开盘推送
- 30min: 盘中每 30 分钟推送
- 1h: 盘中每 1 小时推送
- 3h: 盘中每 3 小时推送
- off: 关闭推送

盘中时间: 9:30-11:30, 13:00-15:00
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from app.config import DEFAULT_USER_ID

scheduler = AsyncIOScheduler()

TRADING_SESSIONS = [(9, 30, 11, 30), (13, 0, 15, 0)]

_last_push_at: str | None = None
_last_push_result: str | None = None

_CST = timezone(timedelta(hours=8))


def _now_cst() -> datetime:
    return datetime.now(_CST)


def _is_trading_day() -> bool:
    from app.data import market_data
    return market_data.is_trading_day()


def _is_trading_time() -> bool:
    if not _is_trading_day():
        return False
    now = _now_cst()
    h, m = now.hour, now.minute
    t = h * 60 + m
    for sh, sm, eh, em in TRADING_SESSIONS:
        if sh * 60 + sm <= t <= eh * 60 + em:
            return True
    return False


async def _get_push_data() -> dict | None:
    """获取默认用户的关注列表和推送配置"""
    from app.engine.memory import memory_store

    try:
        watchlist = await memory_store.get_skill(DEFAULT_USER_ID, "portfolio", "watchlist")
        push_config = await memory_store.get_skill(DEFAULT_USER_ID, "portfolio", "push_config") or {}
        if watchlist:
            return {
                "watchlist": watchlist,
                "frequency": push_config.get("frequency", "open_only"),
            }
    except Exception as e:
        logger.error(f"[定时推送] 获取推送数据失败: {e}", exc_info=True)

    return None


async def _send_push(
    msg: str,
    freq: str = "open_only",
    *,
    with_buttons: bool = True,
    target_override: str | None = None,
    image_b64_list: list[str] | None = None,
):
    """通过已保存的渠道路由发送推送消息。

    Args:
        with_buttons: True → 卡片带频率设置按钮（开盘/手动推送），False → 纯内容（盘中推送）。
        target_override: 指定推送目标，None 则从 portfolio.push_config 读取。
        image_b64_list: base64 PNG 图片列表，附加到卡片底部。
    """
    from app.engine.memory import memory_store
    from app.channels.base import channel_registry

    if target_override:
        target = target_override
    else:
        push_config = await memory_store.get_skill(DEFAULT_USER_ID, "portfolio", "push_config") or {}
        target = push_config.get("target", "private")
        if target == "private":
            alert_cfg = await memory_store.get_skill(DEFAULT_USER_ID, "stock_alert", "alert_config") or {}
            target = alert_cfg.get("push_target_portfolio") or target

    routing = await memory_store.get_global(DEFAULT_USER_ID, "feishu_routing")
    ch = channel_registry.get("feishu")
    if not ch:
        return

    try:
        from app.channels.feishu import FeishuChannel
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
        import asyncio
        import json

        image_keys: list[str] = []
        if image_b64_list:
            for b64 in image_b64_list:
                try:
                    key = await ch._upload_image_b64(b64)
                    if key:
                        image_keys.append(key)
                except Exception as e:
                    logger.warning(f"[定时推送] 图片上传失败: {e}")

        if with_buttons:
            card_json = FeishuChannel.build_report_card(msg, freq)
        else:
            elements: list[dict] = [{"tag": "markdown", "content": msg}]
            for key in image_keys:
                elements.append({
                    "tag": "img",
                    "img_key": key,
                    "alt": {"tag": "plain_text", "content": "预警图表"},
                })
            card = {"config": {"wide_screen_mode": True}, "elements": elements}
            card_json = json.dumps(card, ensure_ascii=False)

        if target and target.startswith("oc_"):
            receive_id_type, receive_id = "chat_id", target
        elif routing and routing.get("open_id"):
            receive_id_type, receive_id = "open_id", routing["open_id"]
        else:
            logger.warning("[定时推送] 无可用推送目标")
            return

        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("interactive")
                .content(card_json)
                .build()
            ).build()
        )
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, ch._lark_client.im.v1.message.create, request,
        )
        if response.success():
            logger.info(f"[定时推送] 飞书发送成功 → {receive_id_type}={receive_id[:20]} (图{len(image_keys)}张)")
            return
        logger.warning(f"[定时推送] 飞书发送失败: {response.code} {response.msg}")
    except Exception as e:
        logger.warning(f"[定时推送] 飞书发送异常: {e}")


def _record_push(result: str = "success") -> None:
    global _last_push_at, _last_push_result
    _last_push_at = _now_cst().strftime("%Y-%m-%d %H:%M:%S")
    _last_push_result = result


async def push_open_report():
    """9:25 开盘推送"""
    if not _is_trading_day():
        return

    logger.info("[定时推送] 9:25 开盘推送开始")
    from app.skills.finance.portfolio import PortfolioSkill, generate_commentary

    data = await _get_push_data()
    if not data or data["frequency"] == "off":
        return

    skill = PortfolioSkill()
    report = skill._fetch_report(data["watchlist"])
    if report.success and report.summary:
        commentary = await generate_commentary(
            data["watchlist"],
            report.summary,
            report.data.get("current_prices", {}),
            is_open=True,
        )
        msg = f"📊 开盘快报\n\n{report.summary}"
        if commentary:
            msg += f"\n\n📝 播报\n{commentary}"
        await _send_push(msg, data["frequency"], with_buttons=False)
        _record_push()
        logger.info("[定时推送] 9:25 完成")


async def push_intraday_report():
    """盘中定时推送 — 根据频率配置"""
    if not _is_trading_time():
        return

    from app.skills.finance.portfolio import PortfolioSkill, PUSH_PRESETS, generate_commentary

    data = await _get_push_data()
    if not data:
        return

    freq = data["frequency"]
    if freq in ("off", "open_only"):
        return
    preset = PUSH_PRESETS.get(freq)
    if not preset or not preset["minutes"]:
        return

    now = _now_cst()
    current_min = now.hour * 60 + now.minute
    interval = preset["minutes"][0]

    if current_min < 9 * 60 + 25 + interval:
        return

    minutes_since_open = (now.hour - 9) * 60 + now.minute - 30
    if now.hour >= 13:
        minutes_since_open = (now.hour - 13) * 60 + now.minute + 120

    if minutes_since_open % interval >= 5:
        return

    skill = PortfolioSkill()
    report = skill._fetch_report(data["watchlist"], intraday=True)
    if report.success and report.summary:
        commentary = await generate_commentary(
            data["watchlist"],
            report.summary,
            report.data.get("current_prices", {}),
            is_open=False,
        )
        msg = f"📊 {report.summary}"
        if commentary:
            msg += f"\n\n📝 播报\n{commentary}"
        await _send_push(msg, freq, with_buttons=False)
        _record_push()
        logger.info("[定时推送] 盘中推送完成")


async def _warmup_dp_cache():
    """启动时一次性预热双紫缓存，Dashboard 首次打开不用等。"""
    import asyncio
    from app.skills.finance.stock_alert import (
        get_watchlist, get_alert_config, get_dp_today_slot,
    )
    try:
        watchlist = await get_watchlist()
        if not watchlist:
            return
        config = await get_alert_config()
        if not config.get("dp_enabled", True):
            return
        loop = asyncio.get_event_loop()
        for item in watchlist:
            await loop.run_in_executor(None, get_dp_today_slot, item["code"], config)
        logger.info(f"[启动预热] 双紫缓存已填充 ({len(watchlist)} 只)")
    except Exception as e:
        logger.warning(f"[启动预热] 双紫缓存预热失败: {e}")


def _alert_type_to_freq_key(alert_type: str) -> str:
    """Map alert type to per-type frequency config key."""
    if alert_type == "double_purple":
        return "freq_dp"
    if alert_type in ("inflow", "outflow"):
        return "freq_flow"
    return "freq_trend"


def _should_push_alert(alert_type: str, config: dict) -> bool:
    """Check if this alert type should push based on per-type frequency setting.

    Uses clock-aligned time windows (5-minute tolerance for cron jitter):
      30min → push at :00-:04 and :30-:34
      60min → push at :00-:04
      day   → push only at 09:30-09:34
      off   → never
    """
    key = _alert_type_to_freq_key(alert_type)
    freq = config.get(key, "30min")
    if freq == "off":
        return False
    now = _now_cst()
    if freq == "day":
        return now.hour == 9 and 30 <= now.minute < 35
    if freq == "60min":
        return now.minute % 60 < 5
    if freq == "30min":
        return now.minute % 30 < 5
    return True


async def push_stock_alerts():
    """盘中定时主力资金预警 — 按类型独立频率过滤。"""
    if not _is_trading_time():
        return

    from app.skills.finance.stock_alert import (
        run_scheduled_check, format_push_message, get_alert_config,
        render_push_chart,
    )
    from app.data import market_data

    try:
        alerts = await run_scheduled_check()
        if not alerts:
            return

        config = await get_alert_config()

        filtered = [a for a in alerts if _should_push_alert(a["type"], config)]
        if not filtered:
            skipped = [a["type"] for a in alerts]
            logger.info(f"[预警推送] 按频率过滤后无需推送 (原 {len(alerts)} 条: {skipped})")
            return
        alerts = filtered

        target = config.get("push_target_alert") or config.get("push_target") or None

        charts: list[str] = []
        loop = asyncio.get_running_loop()
        for a in alerts:
            try:
                ts_code_6 = a["code"]
                ts_suffix = ".SH" if ts_code_6.startswith("6") else ".SZ"
                ts_code = ts_code_6 + ts_suffix

                price_hist = await loop.run_in_executor(None, market_data.get_price, ts_code, 14)
                dp_hist = await loop.run_in_executor(None, market_data.get_main_force, ts_code, 14)

                b64 = await loop.run_in_executor(
                    None, render_push_chart, a, price_hist, dp_hist,
                )
                charts.append(b64)
            except Exception as e:
                logger.warning(f"[预警推送] 图表生成失败 {a.get('name','?')}: {e}")

        msg = format_push_message(alerts)
        await _send_push(
            msg, "alert",
            with_buttons=False,
            target_override=target,
            image_b64_list=charts if charts else None,
        )
        logger.info(f"[预警推送] 发送 {len(alerts)} 条预警 + {len(charts)} 张图")
    except Exception as e:
        logger.error(f"[预警推送] 失败: {e}", exc_info=True)


async def push_market_scan(label: str = "收盘"):
    """全市场双紫扫描 + 推送。"""
    if not _is_trading_day():
        return

    from app.skills.finance.stock_alert import (
        scan_market_dp, format_scan_message, get_alert_config,
    )

    try:
        config = await get_alert_config()
        target = config.get("push_target_scan") or config.get("push_target") or None

        if label == "开盘" and not config.get("push_market_scan_open", True):
            logger.info("[全市场扫描] 开盘扫描推送已关闭")
            return
        if label == "收盘" and not config.get("push_market_scan_close", True):
            logger.info("[全市场扫描] 收盘扫描推送已关闭")
            return
        if label == "盘中" and not config.get("push_market_scan_intraday", False):
            logger.info("[全市场扫描] 盘中扫描推送已关闭")
            return

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, scan_market_dp, config)

        hits = result.get("hits", [])
        if not hits:
            logger.info(f"[全市场扫描] {label} 无双紫命中")
            return

        msg = format_scan_message(result)
        await _send_push(msg, "alert", with_buttons=False, target_override=target)
        logger.info(f"[全市场扫描] {label} 推送 {len(hits)} 只双紫")
    except Exception as e:
        logger.error(f"[全市场扫描] {label} 失败: {e}", exc_info=True)


async def push_market_scan_open():
    await push_market_scan("开盘")


async def push_market_scan_intraday():
    await push_market_scan("盘中")


async def push_market_scan_close():
    await push_market_scan("收盘")


# ── 推送时间线计算 ──────────────────────────

def _next_trading_day(d: datetime) -> datetime:
    """下一个交易日的 0:00"""
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt.replace(hour=0, minute=0, second=0, microsecond=0)


def compute_today_schedule(freq: str) -> list[dict]:
    """计算今天的推送时间线节点列表。

    返回 [{"time": "09:25", "label": "开盘推送", "done": bool}, ...]
    """
    now = _now_cst()
    if now.weekday() >= 5:
        return []
    if freq == "off":
        return []

    nodes: list[dict] = []

    nodes.append({
        "time": "09:25",
        "label": "开盘推送",
        "done": now.hour * 60 + now.minute > 9 * 60 + 25,
    })

    if freq == "open_only":
        return nodes

    from app.skills.finance.portfolio import PUSH_PRESETS
    preset = PUSH_PRESETS.get(freq)
    if not preset or not preset["minutes"]:
        return nodes

    interval = preset["minutes"][0]
    open_push_min = 9 * 60 + 25
    earliest_intraday = open_push_min + interval

    for sh, sm, eh, em in TRADING_SESSIONS:
        t = sh * 60 + sm
        end = eh * 60 + em
        while t <= end:
            if t >= earliest_intraday:
                h, m = divmod(t, 60)
                time_str = f"{h:02d}:{m:02d}"
                current_min = now.hour * 60 + now.minute
                nodes.append({
                    "time": time_str,
                    "label": "盘中推送",
                    "done": current_min > t,
                })
            t += interval

    return nodes


def get_next_push_time(freq: str) -> str | None:
    """计算下一次推送时间，返回 "HH:MM" 或 "明天 HH:MM" 或 None。"""
    if freq == "off":
        return None

    now = _now_cst()
    nodes = compute_today_schedule(freq)
    for n in nodes:
        if not n["done"]:
            return n["time"]

    if now.weekday() >= 5:
        return None

    nxt = _next_trading_day(now)
    wd_name = ["周一", "周二", "周三", "周四", "周五"][nxt.weekday()]
    return f"{wd_name} 09:25"


async def get_push_status() -> dict:
    """返回推送调度的完整状态信息。"""
    data = await _get_push_data()
    freq = data["frequency"] if data else "open_only"

    nodes = compute_today_schedule(freq)
    next_time = get_next_push_time(freq)

    from app.skills.finance.portfolio import PUSH_PRESETS
    freq_label = PUSH_PRESETS.get(freq, {}).get("label", freq)

    return {
        "frequency": freq,
        "frequency_label": freq_label,
        "is_trading_day": _is_trading_day(),
        "is_trading_time": _is_trading_time(),
        "last_push_at": _last_push_at,
        "last_push_result": _last_push_result,
        "next_push_time": next_time,
        "schedule": nodes,
        "server_time": _now_cst().strftime("%Y-%m-%d %H:%M:%S"),
    }


async def execute_push_now() -> dict:
    """立即执行一次推送，返回结果。"""
    from app.skills.finance.portfolio import PortfolioSkill, generate_commentary

    data = await _get_push_data()
    if not data or not data["watchlist"]:
        return {"success": False, "error": "关注列表为空"}

    is_intraday = _is_trading_time()
    skill = PortfolioSkill()
    report = skill._fetch_report(data["watchlist"], intraday=is_intraday)
    if not report.success or not report.summary:
        return {"success": False, "error": "获取行情失败"}

    commentary = await generate_commentary(
        data["watchlist"],
        report.summary,
        report.data.get("current_prices", {}),
        is_open=not is_intraday,
    )
    msg = f"📊 {report.summary}"
    if commentary:
        msg += f"\n\n📝 播报\n{commentary}"

    freq = data.get("frequency", "open_only")
    await _send_push(msg, freq, with_buttons=False)
    _record_push("manual")
    return {"success": True, "message": "推送已发送"}


def start_scheduler():
    scheduler.add_job(
        push_open_report,
        trigger=CronTrigger(hour=9, minute=25, timezone="Asia/Shanghai"),
        id="open_report",
        replace_existing=True,
    )

    scheduler.add_job(
        push_intraday_report,
        trigger=CronTrigger(minute="*/5", timezone="Asia/Shanghai"),
        id="intraday_report",
        replace_existing=True,
    )

    scheduler.add_job(
        push_stock_alerts,
        trigger=CronTrigger(minute="*/5", timezone="Asia/Shanghai"),
        id="stock_alert",
        replace_existing=True,
    )

    scheduler.add_job(
        _warmup_dp_cache,
        trigger="date",
        id="dp_warmup",
        replace_existing=True,
    )

    scheduler.add_job(
        push_market_scan_open,
        trigger=CronTrigger(hour=9, minute=26, timezone="Asia/Shanghai"),
        id="market_scan_open",
        replace_existing=True,
    )

    scheduler.add_job(
        push_market_scan_intraday,
        trigger=CronTrigger(hour=13, minute=0, timezone="Asia/Shanghai"),
        id="market_scan_intraday",
        replace_existing=True,
    )

    scheduler.add_job(
        push_market_scan_close,
        trigger=CronTrigger(hour=15, minute=30, timezone="Asia/Shanghai"),
        id="market_scan_close",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("定时任务已启动 (开盘9:25 + 全市场扫描9:26/13:00/15:30 + 盘中5min检查 + 预警推送 + 双紫预热)")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("定时任务已关闭")
