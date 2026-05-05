"""主力资金预警 Skill — 三层分辨率 + 双紫信号监控持仓股票。

三层指标体系:
  Layer 1 — 盘中实时 (30min): 主力净流入占比超阈值 → 即时预警
  Layer 2 — 日频趋势 (daily):  连续N日资金流方向/加速/反转 → 趋势信号
  Layer 3 — 周频背景 (weekly):  5日累计资金流 + 方向一致性 → 背景判断

双紫信号:
  主力状态紫柱: 超大单买入占比 > 阈值 (主动抢筹)
  主力动向紫柱: 超大单买入占总成交额 > 阈值
  双紫 = 两项同时满足 → 高优先级预警

预警类型:
  🔴 主力大额流入   🟢 主力大额流出
  📈 连续流入趋势   📉 连续流出趋势
  ⚡ 资金流反转     🟣 双紫抢筹信号

回测功能: 查看过去 7 天的预警统计、示例分析和图表
"""

from __future__ import annotations

import asyncio
import base64
import io
import time as _time_mod
from datetime import datetime, timedelta, timezone
from typing import Any

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd
from loguru import logger

from app.config import DEFAULT_USER_ID
import app.engine.memory as _mem_module
from app.data import market_data
from app.skills.base import Skill, SkillCategory, SkillContext, SkillResult, skill

matplotlib.rcParams["font.sans-serif"] = [
    "Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Sans CJK",
    "WenQuanYi Micro Hei", "SimHei", "Microsoft YaHei", "DejaVu Sans",
]
matplotlib.rcParams["axes.unicode_minus"] = False

_CST = timezone(timedelta(hours=8))
ALERT_CONFIG_KEY = "alert_config"

_DEFAULTS = {
    "alert_inflow": True,
    "alert_outflow": True,
    "check_interval": 30,
    "inflow_threshold": 8.0,
    "outflow_threshold": -8.0,
    "push_target": "private",
    "dp_enabled": True,
    "dp_elg_buy_ratio": 70.0,
    "dp_main_turnover_ratio": 20.0,
    "data_mode": "auto",
    "push_daily_alert": True,
    "push_intraday_alert": True,
    "push_market_scan_open": True,
    "push_market_scan_close": True,
    "push_market_scan_intraday": False,
    "scan_min_market_cap": 100.0,
    "scan_min_amount": 5000.0,
    "scan_exclude_st": True,
    "freq_flow": "30min",
    "freq_dp": "30min",
    "freq_trend": "day",
    "push_target_alert": "private",
    "push_target_scan": "private",
    "push_target_portfolio": "private",
}

_last_alert_check: datetime | None = None

# 本模块级缓存（仅用于 slot 去重 / zhuli_status / scan 结果等模块特有逻辑）
# 资金流 / 分单历史数据缓存已移入 market.py._rolling
_rolling_cache: dict[str, dict] = {}


def _now_cst() -> datetime:
    return datetime.now(_CST)


def _dp_slot(interval: int = 30) -> str:
    """当前 30 分钟时间槽，如 '1000', '1030', '1100'。"""
    now = _now_cst()
    minute = (now.minute // interval) * interval
    return f"{now.hour:02d}{minute:02d}"


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# ── 双紫实时槽 ─────────────────────────────────────


def get_dp_today_slot(code: str, config: dict) -> tuple[dict, str]:
    """按 30 分钟时间槽获取单只股票的盘中双紫状态。

    同一时间槽内只拉取一次，后续请求直接返回缓存。
    Returns: (dp_dict, source_str)
    """
    slot = _dp_slot(config.get("check_interval", 30))
    key = f"dp_today:{code}"
    cached = _rolling_cache.get(key)
    if cached and cached.get("slot") == slot:
        return cached["data"], cached.get("source", "slot_cache")

    dp: dict = {}
    source = "none"
    try:
        mf = market_data.get_main_force(code, days=0)
        if mf:
            rec = mf[0]
            dp = {
                "main_turnover_ratio": rec.get("main_turnover_ratio", 0),
                "trend_purple": rec.get("main_turnover_ratio", 0) > config.get("dp_main_turnover_ratio", 20.0),
                "double_purple": False,
            }
            source = "market_data"
    except Exception:
        if cached:
            return cached["data"], "stale_cache"

    _rolling_cache[key] = {
        "data": dp, "source": source, "slot": slot, "ts": _time_mod.time(),
    }
    return dp, source


# ── 双紫计算 ──────────────────────────────────────


def compute_double_purple(info: dict, config: dict) -> dict:
    """计算主力动向指标。

    接受 get_main_force() 返回的 record 或旧格式的 EM dict。

    Returns:
        {
            "main_turnover_ratio": float, 超大单买入占全部买入(%)
            "trend_purple": bool,         动向紫柱（>阈值）
            "double_purple": bool,        双紫（需外部结合主力状态紫柱判定）
        }
    """
    if "main_turnover_ratio" in info:
        ratio = info["main_turnover_ratio"]
    else:
        elg_buy = info.get("elg_buy", 0)
        lg_buy = info.get("lg_buy", 0)
        md_buy = info.get("md_buy", 0)
        sm_buy = info.get("sm_buy", 0)
        total_buy = elg_buy + lg_buy + md_buy + sm_buy
        ratio = (elg_buy / total_buy * 100) if total_buy > 0 else 0

    th_main = config.get("dp_main_turnover_ratio", 20.0)
    return {
        "main_turnover_ratio": round(ratio, 2),
        "trend_purple": ratio > th_main,
        "double_purple": False,
    }


# ── 筹码分布 & 主力状态 ──────────────────────────────


def _build_chip_distribution(h: "np.ndarray", l: "np.ndarray", c: "np.ndarray",
                              vol: "np.ndarray", turnover: "np.ndarray | None" = None,
                              bins: int = 200):
    """构建筹码分布，返回 WINNER 函数。"""
    import numpy as np

    n = len(c)
    price_min = float(np.nanmin(l)) * 0.8
    price_max = float(np.nanmax(h)) * 1.2
    if price_max <= price_min:
        price_max = price_min + 1.0
    edges = np.linspace(price_min, price_max, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    chip = np.zeros(bins, dtype=float)
    chip[bins // 2] = 1.0

    winner_arr = np.zeros(n, dtype=float)

    for i in range(n):
        if turnover is not None and not np.isnan(turnover[i]) and turnover[i] > 0:
            tr = min(turnover[i] / 100.0, 0.5)
        else:
            tr = 0.02

        chip *= (1.0 - tr)

        lo, hi, avg = float(l[i]), float(h[i]), float((h[i] + l[i] + c[i]) / 3)
        if hi <= lo:
            hi = lo + 0.01

        mask = (centers >= lo) & (centers <= hi)
        if not np.any(mask):
            idx_nearest = np.argmin(np.abs(centers - avg))
            chip[idx_nearest] += tr
        else:
            weights = np.zeros(bins)
            weights[mask] = 1.0 + 4.0 * np.maximum(0, 1.0 - np.abs(centers[mask] - avg) / max(hi - lo, 0.01)) ** 2
            wsum = weights.sum()
            if wsum > 0:
                chip += tr * weights / wsum

        total = chip.sum()
        if total > 0:
            cum = np.cumsum(chip)
            idx_c = np.searchsorted(centers, float(c[i]))
            idx_c = min(idx_c, bins - 1)
            winner_arr[i] = cum[idx_c] / total
        else:
            winner_arr[i] = 0.5

    return winner_arr, centers, chip


def compute_zhuli_status(ts_code: str, days: int = 14) -> list[dict]:
    """计算主力状态（基于 WINNER 筹码分布公式）。

    数据来源: market_data.get_price() — 自动包含今日实时。
    """
    import numpy as np

    _full_hist_days = (_now_cst() - datetime(2000, 1, 1, tzinfo=_CST)).days
    records = market_data.get_price(ts_code, days=_full_hist_days)
    if not records or len(records) < 60:
        return []

    dates_list = [r["date"] for r in records]
    c = np.array([r["close"] for r in records], dtype=float)
    h = np.array([r["high"] for r in records], dtype=float)
    l = np.array([r["low"] for r in records], dtype=float)
    vol = np.array([r["volume"] for r in records], dtype=float)
    n = len(c)

    turnover = None
    try:
        basic_df = market_data.get_daily_basic(
            ts_code=ts_code, days=_full_hist_days,
            fields="trade_date,turnover_rate",
        )
        if basic_df is not None and not basic_df.empty:
            tr_map = dict(zip(basic_df["trade_date"], basic_df["turnover_rate"].values))
            turnover = np.array([tr_map.get(d, np.nan) for d in dates_list])
    except Exception:
        pass

    def ema(arr, period):
        out = np.zeros_like(arr, dtype=float)
        k = 2.0 / (period + 1)
        out[0] = arr[0]
        for i in range(1, len(arr)):
            out[i] = arr[i] * k + out[i - 1] * (1 - k)
        return out

    def ref(arr, period):
        out = np.full_like(arr, np.nan, dtype=float)
        if period < len(arr):
            out[period:] = arr[:-period]
        return out

    winner_c, chip_centers, _ = _build_chip_distribution(h, l, c, vol, turnover)

    winner_110 = np.zeros(len(c))
    winner_090 = np.zeros(len(c))
    chip_state = np.zeros(len(chip_centers), dtype=float)
    chip_state[len(chip_centers) // 2] = 1.0
    for i in range(len(c)):
        if turnover is not None and not np.isnan(turnover[i]) and turnover[i] > 0:
            tr = min(turnover[i] / 100.0, 0.5)
        else:
            tr = 0.02
        chip_state *= (1.0 - tr)
        lo_v, hi_v, avg_v = float(l[i]), float(h[i]), float((h[i]+l[i]+c[i])/3)
        if hi_v <= lo_v:
            hi_v = lo_v + 0.01
        mask = (chip_centers >= lo_v) & (chip_centers <= hi_v)
        if np.any(mask):
            w = np.zeros(len(chip_centers))
            w[mask] = 1.0 + 4.0 * np.maximum(0, 1.0 - np.abs(chip_centers[mask] - avg_v) / max(hi_v - lo_v, 0.01)) ** 2
            ws = w.sum()
            if ws > 0:
                chip_state += tr * w / ws
        total = chip_state.sum()
        if total > 0:
            cum = np.cumsum(chip_state)
            idx_110 = min(np.searchsorted(chip_centers, float(c[i] * 1.1)), len(chip_centers) - 1)
            idx_090 = min(np.searchsorted(chip_centers, float(c[i] * 0.9)), len(chip_centers) - 1)
            winner_110[i] = cum[idx_110] / total
            winner_090[i] = cum[idx_090] / total

    zlcm = ema(winner_c * 7, 3)
    shcm = ema((winner_110 - winner_090) * 8, 3)
    total_cm = zlcm + shcm
    total_cm = np.where(total_cm == 0, 1e-9, total_cm)

    zshtl = shcm / total_cm * 10
    zl_ratio = zlcm / total_cm

    mid_strong = np.where(zl_ratio * 20 - 4 > 0, 2 * (zl_ratio * 20 - 4), 0.0)

    zzljj = ema(mid_strong, 89)
    zjlrqd_raw = mid_strong - zzljj
    zjlrqd = np.where(zjlrqd_raw > 0, np.floor(zjlrqd_raw) - 1, 0.0)
    zjlrqd = np.maximum(zjlrqd, 0.0)

    ref_c1 = ref(c, 1)
    short_strong = np.where((zjlrqd > 0) & (c > ref_c1), zjlrqd * 1.5, 0.0)

    mid_control = np.floor(mid_strong) / 5.0

    short_oversold = np.where(-zjlrqd_raw > 0, zjlrqd_raw, 0.0)

    cutoff = max(0, n - days)
    results = []
    for i in range(cutoff, n):
        ms = round(float(mid_strong[i]), 2)
        ss = round(float(short_strong[i]), 2)
        mc = round(float(mid_control[i]), 2)
        so = round(float(short_oversold[i]), 2)
        st = round(float(zshtl[i]), 2)

        if ss > 0:
            state, color = "短线强势", "#ff00ff"
        elif mc > 0 and ms > 0:
            state, color = "中线控盘", "#ffff00"
        elif ms > 0:
            state, color = "中线强势", "#ff0000"
        elif so < 0:
            state, color = "短线超跌", "#0088ff"
        else:
            state, color = "无", "#333333"

        results.append({
            "date": dates_list[i],
            "中线强势": ms,
            "短线强势": ss,
            "中线控盘": mc,
            "短线超跌": so,
            "散户套牢": st,
            "state": state,
            "color": color,
        })

    return results


# ── 趋势分析 ──────────────────────────────────────


def _analyze_trend_records(records: list[dict]) -> dict:
    """Layer 2+3: 分析日频趋势和周频背景。

    接受 market_data.get_fund_flow() 返回的 list[dict]。

    Returns:
        {
            "consecutive_days": int,    正=连续流入天数, 负=连续流出天数
            "direction": "in" | "out" | "mixed",
            "accelerating": bool,       今日流量 > 昨日 * 1.3
            "reversal": bool,           方向刚反转(前3日同向,今日反)
            "week_total": float,        近5日累计净流入
            "week_pct_avg": float,      近5日平均占比%
            "week_consistent": bool,    近5日中>=4日同向
        }
    """
    if len(records) < 2:
        return {}

    pcts = [_safe_float(r.get("net_pct", 0)) for r in records[-6:]]
    amts = [_safe_float(r.get("net_amount", 0)) for r in records[-6:]]

    recent5_pcts = pcts[-5:] if len(pcts) >= 5 else pcts
    recent5_amts = amts[-5:] if len(amts) >= 5 else amts

    consec = 0
    if len(pcts) >= 2:
        if pcts[-1] > 0:
            consec = 1
            for v in reversed(pcts[:-1]):
                if v > 0:
                    consec += 1
                else:
                    break
        elif pcts[-1] < 0:
            consec = -1
            for v in reversed(pcts[:-1]):
                if v < 0:
                    consec -= 1
                else:
                    break

    accelerating = False
    if len(amts) >= 2 and amts[-2] != 0:
        ratio = abs(amts[-1]) / abs(amts[-2]) if abs(amts[-2]) > 0 else 0
        accelerating = ratio > 1.3 and (amts[-1] * amts[-2] > 0)

    reversal = False
    if len(pcts) >= 4:
        prev3 = pcts[-4:-1]
        if all(v > 0 for v in prev3) and pcts[-1] < 0:
            reversal = True
        elif all(v < 0 for v in prev3) and pcts[-1] > 0:
            reversal = True

    direction = "in" if consec > 0 else ("out" if consec < 0 else "mixed")

    week_total = sum(recent5_amts)
    week_pct_avg = sum(recent5_pcts) / len(recent5_pcts) if recent5_pcts else 0
    positive_days = sum(1 for v in recent5_pcts if v > 0)
    negative_days = sum(1 for v in recent5_pcts if v < 0)
    week_consistent = positive_days >= 4 or negative_days >= 4

    return {
        "consecutive_days": consec,
        "direction": direction,
        "accelerating": accelerating,
        "reversal": reversal,
        "week_total": week_total,
        "week_pct_avg": week_pct_avg,
        "week_consistent": week_consistent,
    }


async def get_watchlist() -> list[dict]:
    wl = await _mem_module.memory_store.get_skill(DEFAULT_USER_ID, "portfolio", "watchlist")
    return wl or []


async def get_alert_config() -> dict:
    cfg = await _mem_module.memory_store.get_skill(DEFAULT_USER_ID, "stock_alert", ALERT_CONFIG_KEY)
    if not cfg:
        cfg = {}
    for k, v in _DEFAULTS.items():
        cfg.setdefault(k, v)
    return cfg


# ── 实时检测 ────────────────────────────────────────


def _evaluate_alerts(config: dict, watchlist: list[dict]) -> list[dict]:
    """三层融合 + 双紫预警评估。

    所有数据通过 market_data 统一接口获取，自动包含今日实时。
    """
    alerts: list[dict] = []

    inflow_th = config.get("inflow_threshold", 8.0)
    outflow_th = config.get("outflow_threshold", -8.0)
    dp_enabled = config.get("dp_enabled", True)

    for item in watchlist:
        ts_code = item["code"]
        name = item.get("name", ts_code)
        code = ts_code.split(".")[0]

        flow_records = market_data.get_fund_flow(ts_code, days=8)
        if not flow_records:
            continue
        today_flow = flow_records[-1]
        net_pct = _safe_float(today_flow.get("net_pct"))
        net_amount = _safe_float(today_flow.get("net_amount"))
        super_big = _safe_float(today_flow.get("super_big"))
        big = _safe_float(today_flow.get("big"))

        price_snap = market_data.get_price(ts_code, days=0)
        if not price_snap:
            continue
        p = price_snap[0]
        price = _safe_float(p.get("price", p.get("close", 0)))
        pct_chg = _safe_float(p.get("pct_chg", 0))

        trend = _analyze_trend_records(flow_records) if len(flow_records) >= 2 else {}

        dp_info: dict = {}
        if dp_enabled:
            mf_today = market_data.get_main_force(ts_code, days=0)
            if mf_today:
                dp_info = compute_double_purple(mf_today[0], config)

        status_cache = _rolling_cache.get(f"zhuli_status:{ts_code}")
        has_status_purple = False
        if status_cache and isinstance(status_cache, list) and status_cache:
            last_status = status_cache[-1]
            has_status_purple = last_status.get("短线强势", 0) > 0

        if has_status_purple and dp_info.get("trend_purple"):
            dp_info["double_purple"] = True

        base = dict(
            code=code, name=name, price=price, pct_chg=pct_chg,
            net_pct=net_pct, net_amount=net_amount,
            super_big=super_big, big=big, trend=trend, dp=dp_info,
        )

        triggered = False

        if dp_info.get("double_purple"):
            alerts.append({**base, "type": "double_purple", "level": "red"})
            triggered = True

        if not triggered and config.get("alert_inflow", True) and net_pct > inflow_th:
            level = "red" if (net_pct > inflow_th * 1.5 or trend.get("week_consistent")) else "orange"
            alerts.append({**base, "type": "inflow", "level": level})
            triggered = True
        elif not triggered and config.get("alert_outflow", True) and net_pct < outflow_th:
            level = "red" if (net_pct < outflow_th * 1.5 or trend.get("week_consistent")) else "orange"
            alerts.append({**base, "type": "outflow", "level": level})
            triggered = True

        if not triggered and trend:
            if trend.get("reversal"):
                direction = "inflow" if net_pct > 0 else "outflow"
                alerts.append({**base, "type": f"reversal_{direction}", "level": "orange"})
            elif abs(trend.get("consecutive_days", 0)) >= 3 and trend.get("accelerating"):
                direction = "trend_in" if trend["consecutive_days"] > 0 else "trend_out"
                alerts.append({**base, "type": direction, "level": "orange"})

    return alerts


def _fmt_amt(val: float) -> str:
    """元 → 可读金额（万/亿），不带正号。"""
    a = abs(val)
    if a >= 1e8:
        return f"{val / 1e8:.2f}亿"
    if a >= 1e4:
        return f"{val / 1e4:.0f}万"
    return f"{val:.0f}"


def _format_alert(a: dict) -> str:
    _TYPE_LABEL = {
        "double_purple": ("🟣🟣", "双紫抢筹"),
        "inflow": ("🔴", "主力流入"),
        "outflow": ("🟢", "主力流出"),
        "reversal_inflow": ("⚡", "反转流入"),
        "reversal_outflow": ("⚡", "反转流出"),
        "trend_in": ("📈", "趋势流入"),
        "trend_out": ("📉", "趋势流出"),
    }
    icon, label = _TYPE_LABEL.get(a["type"], ("⚠️", a["type"]))
    chg_s = f"{a['pct_chg']:+.1f}%"
    lines = [f"{icon}【{label}】{a['name']}（{a['code']}）¥{a['price']:.2f} ({chg_s})"]

    amt = a["net_amount"]
    amt_s = _fmt_amt(amt)
    pct_s = f"{a['net_pct']:+.1f}%"
    trend = a.get("trend", {})
    trend_parts: list[str] = []
    cd = trend.get("consecutive_days", 0)
    if cd > 0:
        trend_parts.append(f"连续{cd}日")
    elif cd < 0:
        trend_parts.append(f"连续{abs(cd)}日")
    if trend.get("accelerating"):
        trend_parts.append("加速")
    trend_s = "·".join(trend_parts)
    flow_extra = f"  {trend_s}" if trend_s else ""
    lines.append(f"  资金｜{'净流入' if amt>=0 else '净流出'} {amt_s}  占比 {pct_s}{flow_extra}")

    dp = a.get("dp", {})
    if dp and (dp.get("status_purple") or dp.get("trend_purple")):
        s1 = "🟣" if dp.get("status_purple") else "⚪"
        s2 = "🟣" if dp.get("trend_purple") else "⚪"
        lines.append(f"  双紫｜{s1}{s2} 状态 {dp.get('elg_buy_ratio', 0):.0f}%  动向 {dp.get('main_turnover_ratio', 0):.0f}%")

    if trend.get("reversal"):
        direction = "流入" if a["net_pct"] > 0 else "流出"
        lines.append(f"  趋势｜⚡ 前3日反向 → 今日反转{direction}")

    return "\n".join(lines)


# ── 回测 ────────────────────────────────────────────


async def get_alert_snapshots(days: int = 7) -> list[dict]:
    """读取存储的盘中预警快照。"""
    history: list[dict] = await _mem_module.memory_store.get_skill(
        DEFAULT_USER_ID, "stock_alert", "alert_snapshots"
    ) or []
    cutoff = (_now_cst() - timedelta(days=days)).strftime("%Y-%m-%d")
    return [s for s in history if s.get("date", "") >= cutoff]


def _run_backtest(watchlist: list[dict], config: dict, days: int = 7) -> dict:
    eval_cutoff_nd = (_now_cst() - timedelta(days=days)).strftime("%Y%m%d")
    all_alerts: list[dict] = []
    stock_flow_data: dict[str, list[dict]] = {}

    inflow_th = config.get("inflow_threshold", 8.0)
    outflow_th = config.get("outflow_threshold", -8.0)

    dp_by_stock: dict[str, dict[str, dict]] = {}
    status_by_stock: dict[str, dict[str, dict]] = {}

    if config.get("dp_enabled", True):
        for item in watchlist:
            try:
                mf_recs = market_data.get_main_force(item["code"], days + 15)
                dp_map = {}
                for r in mf_recs:
                    dp_map[r["date"]] = compute_double_purple(r, config)
                dp_by_stock[item["code"]] = dp_map
            except Exception:
                pass
            try:
                status_list = compute_zhuli_status(item["code"], days + 15)
                status_map = {s["date"]: s for s in status_list}
                status_by_stock[item["code"]] = status_map
            except Exception:
                pass

    for item in watchlist:
        code = item["code"].split(".")[0]
        name = item["name"]
        try:
            flow_records = market_data.get_fund_flow(item["code"], days=days + 15)
            if not flow_records:
                continue

            eval_records = [r for r in flow_records if r["date"] >= eval_cutoff_nd]
            stock_flow_data[item["code"]] = eval_records

            dp_map = dp_by_stock.get(item["code"], {})

            for rec in eval_records:
                net_pct = _safe_float(rec.get("net_pct"))
                date_nd = rec["date"]
                date_str = f"{date_nd[:4]}-{date_nd[4:6]}-{date_nd[6:]}" if len(date_nd) == 8 else date_nd

                idx_in_flow = flow_records.index(rec)
                history_up_to = flow_records[:idx_in_flow + 1]
                trend = _analyze_trend_records(history_up_to) if len(history_up_to) >= 2 else {}

                dp_info = dp_map.get(date_nd, {})
                status_map = status_by_stock.get(item["code"], {})
                day_status = status_map.get(date_nd, {})
                if day_status.get("短线强势", 0) > 0 and dp_info.get("trend_purple"):
                    dp_info["double_purple"] = True

                base = {
                    "date": date_str,
                    "code": code, "ts_code": item["code"], "name": name,
                    "price": _safe_float(rec.get("close")),
                    "pct_chg": _safe_float(rec.get("pct_chg")),
                    "net_pct": net_pct,
                    "net_amount": _safe_float(rec.get("net_amount")),
                    "super_big": _safe_float(rec.get("super_big")),
                    "big": _safe_float(rec.get("big")),
                    "trend": trend,
                    "dp": dp_info,
                }

                triggered = False

                if dp_info.get("double_purple"):
                    all_alerts.append({**base, "type": "double_purple", "level": "red"})
                    triggered = True

                if not triggered and config.get("alert_inflow", True) and net_pct > inflow_th:
                    level = "red" if (net_pct > inflow_th * 1.5 or trend.get("week_consistent")) else "orange"
                    all_alerts.append({**base, "type": "inflow", "level": level})
                    triggered = True
                elif not triggered and config.get("alert_outflow", True) and net_pct < outflow_th:
                    level = "red" if (net_pct < outflow_th * 1.5 or trend.get("week_consistent")) else "orange"
                    all_alerts.append({**base, "type": "outflow", "level": level})
                    triggered = True

                if not triggered and trend:
                    if trend.get("reversal"):
                        direction = "inflow" if net_pct > 0 else "outflow"
                        all_alerts.append({**base, "type": f"reversal_{direction}", "level": "orange"})
                    elif abs(trend.get("consecutive_days", 0)) >= 3 and trend.get("accelerating"):
                        direction = "trend_in" if trend["consecutive_days"] > 0 else "trend_out"
                        all_alerts.append({**base, "type": direction, "level": "orange"})

        except Exception as e:
            logger.warning(f"[预警回测] {name}({code}) 失败: {e}")

    example = None
    chart_b64 = None
    if all_alerts:
        all_alerts.sort(key=lambda a: abs(a["net_pct"]), reverse=True)
        example = all_alerts[0]
        ts_code = example["ts_code"]
        if ts_code in stock_flow_data:
            alert_dates = [a["date"] for a in all_alerts if a["ts_code"] == ts_code]
            chart_b64 = _render_backtest_chart(
                stock_flow_data[ts_code], example["name"], example["code"], alert_dates,
            )

    type_counts = {}
    for a in all_alerts:
        type_counts[a["type"]] = type_counts.get(a["type"], 0) + 1

    return {
        "total_alerts": len(all_alerts),
        "dp_alerts": type_counts.get("double_purple", 0),
        "inflow_alerts": type_counts.get("inflow", 0),
        "outflow_alerts": type_counts.get("outflow", 0),
        "trend_alerts": type_counts.get("trend_in", 0) + type_counts.get("trend_out", 0),
        "reversal_alerts": type_counts.get("reversal_inflow", 0) + type_counts.get("reversal_outflow", 0),
        "alerts": all_alerts,
        "example": example,
        "chart_b64": chart_b64,
        "days": days,
        "stocks_checked": len(watchlist),
    }


def _render_backtest_chart(
    records: list[dict], name: str, code: str, alert_dates: list[str],
) -> str:
    """回测图表，接受 get_fund_flow() 返回的 list[dict]。"""
    if not records:
        return ""

    def _d(d: str) -> str:
        if len(d) == 8:
            return f"{d[:4]}-{d[4:6]}-{d[6:]}"
        return d

    dates = [_d(r["date"]) for r in records]
    closes = [_safe_float(r.get("close")) for r in records]
    net_flows = [_safe_float(r.get("net_amount")) for r in records]
    net_pcts = [_safe_float(r.get("net_pct")) for r in records]

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(10, 6), height_ratios=[1.2, 1], facecolor="#0d0d0f",
    )
    fig.subplots_adjust(hspace=0.35)

    xs = range(len(dates))
    alert_set = set(alert_dates)

    ax1.set_facecolor("#0d0d0f")
    ax1.plot(xs, closes, color="#e0e0e0", linewidth=1.5, marker="o", markersize=3)
    for i, d in enumerate(dates):
        if d in alert_set:
            ax1.scatter(i, closes[i], color="#ef5350", s=100, zorder=5,
                        marker="v", edgecolors="white", linewidths=0.5)
    ax1.set_title(f"{name}（{code}）近7日 · 价格与主力资金", color="#ccc", fontsize=13, pad=10)
    ax1.set_ylabel("收盘价", color="#888", fontsize=10)
    ax1.tick_params(colors="#666")
    ax1.grid(color="#1a1a1e", linestyle="--", alpha=0.5)
    ax1.set_xticks(list(xs))
    ax1.set_xticklabels(dates, fontsize=8, color="#888", rotation=30)

    ax2.set_facecolor("#0d0d0f")
    bar_colors = ["#ef5350" if v >= 0 else "#26a69a" for v in net_flows]
    ax2.bar(xs, net_flows, color=bar_colors, width=0.6, alpha=0.8)
    ax2.axhline(y=0, color="#444", linewidth=0.5)
    ax2.set_ylabel("主力净流入", color="#888", fontsize=10)
    ax2.tick_params(colors="#666")
    ax2.grid(color="#1a1a1e", linestyle="--", alpha=0.5)

    if any(v != 0 for v in net_pcts):
        ax2t = ax2.twinx()
        ax2t.plot(xs, net_pcts, color="#ffa726", linewidth=1.2, marker="o", markersize=3)
        ax2t.axhline(y=0, color="#444", linewidth=0.3)
        ax2t.set_ylabel("净占比 %", color="#ffa726", fontsize=10)
        ax2t.tick_params(colors="#ffa726")
        ax2t.spines["right"].set_color("#ffa726")

    for ax in (ax1, ax2):
        for spine in ("top", "right", "bottom", "left"):
            ax.spines[spine].set_color("#333")
        ax.spines["top"].set_visible(False)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="#0d0d0f", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def render_push_chart(
    alert: dict,
    price_history: list[dict] | None = None,
    dp_history: list[dict] | None = None,
    chart_days: int = 14,
    status_data: list[dict] | None = None,
) -> str:
    """为单条预警生成推送图（base64 PNG）。

    统一三面板: K线 · 主力状态 · 主力动向，所有预警类型相同。

    Args:
        price_history: market_data.get_price(code, days=N) 输出
        dp_history: market_data.get_main_force(code, days=N) 输出
    """
    import numpy as np

    name, code = alert["name"], alert["code"]
    atype = alert.get("type", "")
    _TYPE_LABEL = {
        "double_purple": "双紫抢筹",
        "inflow": "主力流入",
        "outflow": "主力流出",
        "reversal_inflow": "反转流入",
        "reversal_outflow": "反转流出",
        "trend_in": "趋势流入",
        "trend_out": "趋势流出",
    }
    alert_label = _TYPE_LABEL.get(atype, atype)

    ts_code_full = alert.get("ts_code", "")
    if not ts_code_full or "." not in ts_code_full:
        ts_code_full = code + (".SH" if code.startswith("6") else ".SZ")

    is_inflow = "inflow" in atype or "double_purple" in atype or "trend_in" in atype
    marker_color = "#ef5350" if is_inflow else "#26a69a"

    if price_history is None:
        price_history = market_data.get_price(ts_code_full, days=chart_days)

    if price_history:
        ph = price_history[-chart_days:]
        date_labels = []
        for r in ph:
            d = r["date"]
            if len(d) >= 8:
                date_labels.append(f"{d[4:6]}/{d[6:8]}")
            else:
                date_labels.append(d[-5:])
        closes = pd.Series([_safe_float(r.get("close", r.get("price", 0))) for r in ph])
    else:
        date_labels = [_now_cst().strftime("%m/%d")]
        closes = pd.Series([alert["price"]])

    xs = range(len(date_labels))
    last_i = len(date_labels) - 1

    fig, axes = plt.subplots(
        3, 1, figsize=(9, 7), height_ratios=[1.2, 0.8, 0.8],
        facecolor="#0d0d0f", sharex=False,
    )
    fig.subplots_adjust(hspace=0.12)

    FS_TICK, FS_LABEL, FS_ANNO = 11, 12, 10

    # === Panel 1: K-line / Price ===
    ax1 = axes[0]
    ax1.set_facecolor("#0d0d0f")
    ax1.text(0.01, 0.97, f"{name}（{code}）", transform=ax1.transAxes,
             fontsize=13, color="#e0e0e0", weight="bold", va="top",
             bbox=dict(boxstyle="round,pad=0.3", facecolor="#0d0d0f", edgecolor="#444", alpha=0.9))
    ax1.plot(xs, closes, color="#e0e0e0", linewidth=2, marker="o", markersize=4)
    if len(closes) > 0:
        ax1.scatter([last_i], [closes.iloc[last_i]], color=marker_color, s=160, zorder=5,
                    marker="o", edgecolors="white", linewidths=1.5)
        ax1.annotate(
            f"【{alert_label}】¥{closes.iloc[last_i]:.2f}",
            (last_i, closes.iloc[last_i]),
            textcoords="offset points", xytext=(-70, 16),
            fontsize=FS_ANNO, color=marker_color, weight="bold",
            arrowprops=dict(arrowstyle="->", color=marker_color, lw=1.2),
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#0d0d0f", edgecolor=marker_color, alpha=0.9),
        )
    ax1.set_xticklabels([]); ax1.tick_params(colors="#888", labelsize=FS_TICK)
    ax1.set_ylabel("价格", color="#aaa", fontsize=FS_LABEL)
    ax1.grid(color="#1a1a1e", linestyle="--", alpha=0.5)
    if len(closes) >= 2:
        ax1.set_ylim(closes.min() * 0.98, closes.max() * 1.02)

    # === Panel 2: 主力状态 ===
    ax2 = axes[1]
    ax2.set_facecolor("#0d0d0f")
    if status_data is None:
        try:
            status_data = compute_zhuli_status(ts_code_full, days=chart_days)
        except Exception as e:
            logger.warning(f"[图表] 主力状态计算失败 {ts_code_full}: {e}")
            status_data = []

    if not status_data:
        ax2.text(0.5, 0.5, "主力状态数据不足", transform=ax2.transAxes,
                 ha="center", va="center", color="#666", fontsize=FS_LABEL)
    else:
        n_s = len(status_data)
        sx = np.arange(n_s)
        v_strong = [d.get("中线强势", 0) for d in status_data]
        v_control = [d.get("中线控盘", 0) for d in status_data]
        v_short = [d.get("短线强势", 0) for d in status_data]
        v_s_over = [d.get("短线超跌", 0) for d in status_data]
        ax2.bar(sx, v_strong, 0.7, color="#ff0000", alpha=0.8, zorder=1)
        ax2.bar(sx, v_control, 0.7, color="#ffff00", alpha=0.85, zorder=2)
        ax2.bar(sx, v_short, 0.7, color="#ff00ff", alpha=0.9, zorder=3)
        ax2.bar(sx, v_s_over, 0.7, color="#0088ff", alpha=0.85, zorder=1)
        last = status_data[-1]
        if last["state"] != "无":
            top_val = max(v_strong[-1], v_control[-1], v_short[-1], abs(v_s_over[-1]))
            ax2.annotate(
                f"{last['state']} {top_val:.1f}",
                (sx[-1], top_val), textcoords="offset points", xytext=(-60, 10),
                fontsize=FS_ANNO, color=last["color"], weight="bold",
                arrowprops=dict(arrowstyle="->", color=last["color"], lw=1.2),
                bbox=dict(boxstyle="round,pad=0.2", facecolor="#0d0d0f", edgecolor=last["color"], alpha=0.9),
            )
        ax2.axhline(y=0, color="#444", linewidth=0.5)
        handles = [plt.Rectangle((0, 0), 1, 1, fc=cc) for cc in ["#ff00ff", "#ff0000", "#ffff00", "#0088ff"]]
        ax2.legend(handles, ["短线强势", "中线强势", "中线控盘", "短线超跌"],
                   fontsize=9, loc="upper left", facecolor="#0d0d0f", edgecolor="#333", labelcolor="#ccc", ncol=4)
        ax2.set_xticklabels([])
    ax2.set_ylabel("主力状态", color="#aaa", fontsize=FS_LABEL)
    ax2.tick_params(colors="#888", labelsize=FS_TICK)
    ax2.grid(color="#1a1a1e", linestyle="--", alpha=0.5, axis="y")

    # === Panel 3: 主力动向 ===
    ax3 = axes[2]
    ax3.set_facecolor("#0d0d0f")

    if dp_history is None:
        dp_history = market_data.get_main_force(ts_code_full, days=chart_days)

    if not dp_history or len(dp_history) < 2:
        ax3.text(0.5, 0.5, "主力动向数据不足", transform=ax3.transAxes,
                 ha="center", va="center", color="#666", fontsize=FS_LABEL)
    else:
        dp_sorted = sorted(dp_history, key=lambda d: d["date"])[-chart_days:]
        n_d = len(dp_sorted)
        dx = np.arange(n_d)
        th_main = _DEFAULTS["dp_main_turnover_ratio"]
        main_r = [d.get("main_turnover_ratio", 0) for d in dp_sorted]
        bar_colors = ["#db2777" if m > th_main else "#f472b6" for m in main_r]
        ax3.bar(dx, main_r, 0.6, color=bar_colors, alpha=0.85)
        ax3.axhline(y=th_main, color="#f472b6", linewidth=1, linestyle="--", alpha=0.6)
        ax3.text(len(dx) - 0.5, th_main + 0.5, f"{th_main:.0f}%", fontsize=9, color="#f472b6", ha="right")
        d_last = n_d - 1
        if d_last >= 0 and main_r[d_last] > th_main:
            ax3.annotate(
                f"动向 {main_r[d_last]:.0f}%",
                (dx[d_last], main_r[d_last]), textcoords="offset points", xytext=(-50, 10),
                fontsize=FS_ANNO, color="#fbbf24", weight="bold",
                arrowprops=dict(arrowstyle="->", color="#fbbf24", lw=1.2),
                bbox=dict(boxstyle="round,pad=0.2", facecolor="#0d0d0f", edgecolor="#fbbf24", alpha=0.9),
            )
        dp_labels = [d["date"][-4:] if len(d["date"]) >= 4 else d["date"] for d in dp_sorted]
        dp_labels = [f"{lb[:2]}/{lb[2:]}" for lb in dp_labels]
        ax3.set_xticks(dx); ax3.set_xticklabels(dp_labels, fontsize=FS_TICK, color="#aaa")
    ax3.set_ylabel("主力动向", color="#aaa", fontsize=FS_LABEL)
    ax3.tick_params(colors="#888", labelsize=FS_TICK)
    ax3.grid(color="#1a1a1e", linestyle="--", alpha=0.5, axis="y")

    for ax in axes:
        for spine in ("top", "right", "bottom", "left"):
            ax.spines[spine].set_color("#333")
        ax.spines["top"].set_visible(False)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="#0d0d0f", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ── 全市场双紫扫描 ──────────────────────────────────

def _calc_consecutive_dp_days(
    pro, ts_codes: list[str], trade_date: str, th_elg: float, th_main: float, max_lookback: int = 30
) -> dict[str, int]:
    """回溯计算命中股票的连续双紫天数（含当日）。"""
    result: dict[str, int] = {c: 1 for c in ts_codes}
    remaining = set(ts_codes)
    dt = datetime.strptime(trade_date, "%Y%m%d")

    for day_offset in range(1, max_lookback + 1):
        if not remaining:
            break
        dt -= timedelta(days=1)
        if dt.weekday() >= 5:
            continue
        prev_date = dt.strftime("%Y%m%d")
        try:
            df = market_data.get_main_force_market(prev_date)
        except Exception:
            break
        if df is None or df.empty:
            continue

        dp_set: set[str] = set()
        for _, r in df.iterrows():
            code = r["ts_code"]
            if code not in remaining:
                continue
            elg_buy = _safe_float(r.get("buy_elg_amount"))
            lg_buy = _safe_float(r.get("buy_lg_amount"))
            md_buy = _safe_float(r.get("buy_md_amount"))
            sm_buy = _safe_float(r.get("buy_sm_amount"))
            total_buy = elg_buy + lg_buy + md_buy + sm_buy
            ratio2 = (elg_buy / total_buy * 100) if total_buy > 0 else 0
            if ratio2 > th_main:
                dp_set.add(code)

        broken = remaining - dp_set
        remaining -= broken
        for code in remaining:
            result[code] += 1

    return result


def scan_market_dp(config: dict | None = None) -> dict:
    """全市场双紫扫描（日线级别）。"""
    if config is None:
        config = dict(_DEFAULTS)

    th_main = config.get("dp_main_turnover_ratio", 20.0)
    min_mv = config.get("scan_min_market_cap", 100.0) * 1e4
    min_amt = config.get("scan_min_amount", 5000.0)
    exclude_st = config.get("scan_exclude_st", True)

    df_mf = None
    trade_date = ""
    for offset in range(5):
        d = (_now_cst() - timedelta(days=offset)).strftime("%Y%m%d")
        df_mf = market_data.get_main_force_market(d)
        if df_mf is not None and not df_mf.empty:
            trade_date = d
            break
    if df_mf is None or df_mf.empty:
        return {"trade_date": trade_date, "total_scanned": 0, "filtered": 0, "hits": []}

    df_basic = None
    try:
        df_basic = market_data.get_daily_basic(
            trade_date=trade_date,
            fields="ts_code,close,total_mv,circ_mv,turnover_rate",
        )
    except Exception as e:
        logger.warning(f"[全市场扫描] daily_basic 获取失败: {e}")

    mv_map: dict[str, dict] = {}
    if df_basic is not None and not df_basic.empty:
        for _, r in df_basic.iterrows():
            mv_map[r["ts_code"]] = {
                "total_mv": _safe_float(r.get("total_mv")),
                "close": _safe_float(r.get("close")),
                "circ_mv": _safe_float(r.get("circ_mv")),
            }

    name_map: dict[str, str] = {}
    cache_key = "stock_name_map"
    cached_names = _rolling_cache.get(cache_key)
    if cached_names:
        name_map = cached_names
    else:
        try:
            df_names = market_data.get_stock_list()
            if df_names is not None and not df_names.empty:
                name_map = dict(zip(df_names["ts_code"], df_names["name"]))
                _rolling_cache[cache_key] = name_map
        except Exception:
            pass

    total = len(df_mf)
    hits: list[dict] = []

    for _, r in df_mf.iterrows():
        ts_code = r["ts_code"]
        name = name_map.get(ts_code, "")

        if exclude_st and ("ST" in name or "st" in name):
            continue

        basic = mv_map.get(ts_code, {})
        total_mv = basic.get("total_mv", 0)
        if min_mv > 0 and total_mv < min_mv:
            continue

        elg_buy = _safe_float(r.get("buy_elg_amount"))
        lg_buy = _safe_float(r.get("buy_lg_amount"))
        md_buy = _safe_float(r.get("buy_md_amount"))
        sm_buy = _safe_float(r.get("buy_sm_amount"))

        total_buy = elg_buy + lg_buy + md_buy + sm_buy
        if min_amt > 0 and total_buy < min_amt:
            continue

        main_turnover_ratio = (elg_buy / total_buy * 100) if total_buy > 0 else 0

        if main_turnover_ratio > th_main:
            hits.append({
                "ts_code": ts_code,
                "code": ts_code.split(".")[0],
                "name": name,
                "close": basic.get("close", 0),
                "total_mv": round(total_mv / 1e4, 1) if total_mv else 0,
                "main_turnover_ratio": round(main_turnover_ratio, 1),
                "elg_buy": round(elg_buy, 2),
                "total_buy": round(total_buy, 2),
            })

    logger.info(f"[全市场扫描] {trade_date} 共{total}只 → 动向过滤{len(hits)}只，开始主力状态二次筛选...")

    if hits:
        confirmed = []
        for h in hits:
            try:
                status = compute_zhuli_status(h["ts_code"], days=1)
                if status and status[-1].get("短线强势", 0) > 0:
                    h["status_value"] = status[-1]["短线强势"]
                    confirmed.append(h)
            except Exception:
                pass
        logger.info(f"[全市场扫描] 主力状态过滤: {len(hits)} → {len(confirmed)} 只双紫确认")
        hits = confirmed

    hits.sort(key=lambda h: h["main_turnover_ratio"], reverse=True)

    for h in hits:
        h["dp_streak"] = 1

    logger.info(f"[全市场扫描] {trade_date} 最终双紫命中{len(hits)}只")

    _rolling_cache["market_scan_last"] = {
        "trade_date": trade_date,
        "hits": hits,
        "total": total,
        "ts": _time_mod.time(),
    }

    return {
        "trade_date": trade_date,
        "total_scanned": total,
        "hits": hits,
    }


def format_scan_message(result: dict) -> str:
    """格式化全市场扫描推送消息。"""
    hits = result.get("hits", [])
    date = result.get("trade_date", "")
    total = result.get("total_scanned", 0)

    if not hits:
        return f"🔍 全市场双紫扫描（{date}）\n\n全市场 {total} 只股票中无双紫信号。"

    lines = [f"🟣🟣 全市场双紫扫描（{date}）— 命中 {len(hits)} 只\n"]
    for h in hits:
        mv_s = f"{h['total_mv']}亿" if h["total_mv"] else ""
        lines.append(
            f"  🟣 {h['name']}（{h['code']}）"
            f" 动向{h['main_turnover_ratio']:.0f}% {mv_s}"
        )
    lines.append(f"\n扫描范围: {total} 只（已过滤ST/小盘/低成交）")
    return "\n".join(lines)


def backtest_dp_signal(lookback_days: int = 60, hold_days: int = 10,
                       config: dict | None = None) -> dict:
    """双紫信号历史回测 — 统计信号触发后N日收益 vs 市场基准。

    双紫 = 主力动向紫柱（超大单占比 > 阈值）+ 主力状态紫柱（短线强势 > 0）。
    先用条件 1 过滤全市场，再对命中股票逐一验证条件 2。

    Returns:
        {"summary": {...}, "daily_results": [...], "signal_details": [...]}
    """
    import numpy as np

    if config is None:
        config = dict(_DEFAULTS)

    th_main = config.get("dp_main_turnover_ratio", 20.0)
    min_mv = config.get("scan_min_market_cap", 100.0) * 1e4
    min_amt = config.get("scan_min_amount", 5000.0)
    exclude_st = config.get("scan_exclude_st", True)

    cal = market_data.get_trade_cal()
    if cal is None or cal.empty:
        return {"error": "无法获取交易日历"}

    today = _now_cst().strftime("%Y%m%d")
    trade_dates = sorted(cal[cal["cal_date"] <= today]["cal_date"].tolist())
    if len(trade_dates) < lookback_days + hold_days:
        return {"error": "交易日历数据不足"}

    scan_dates = trade_dates[-(lookback_days + hold_days):-hold_days]
    future_dates = trade_dates[-hold_days:]

    name_map: dict[str, str] = {}
    try:
        df_names = market_data.get_stock_list()
        if df_names is not None and not df_names.empty:
            name_map = dict(zip(df_names["ts_code"], df_names["name"]))
    except Exception:
        pass

    from app.data.market import _get_pro
    pro = _get_pro()

    logger.info(f"[双紫回测] 开始: {len(scan_dates)} 个交易日, hold={hold_days}d")

    all_candidates: list[dict] = []
    daily_results: list[dict] = []

    for scan_date in scan_dates:
        df_mf = market_data.get_main_force_market(scan_date)
        if df_mf is None or df_mf.empty:
            daily_results.append({"date": scan_date, "hits": 0})
            continue

        df_basic = None
        try:
            df_basic = market_data.get_daily_basic(
                trade_date=scan_date,
                fields="ts_code,close,total_mv",
            )
        except Exception:
            pass

        mv_map: dict[str, dict] = {}
        if df_basic is not None and not df_basic.empty:
            for _, r in df_basic.iterrows():
                mv_map[r["ts_code"]] = {
                    "total_mv": _safe_float(r.get("total_mv")),
                    "close": _safe_float(r.get("close")),
                }

        for _, r in df_mf.iterrows():
            ts_code = r["ts_code"]
            nm = name_map.get(ts_code, "")
            if exclude_st and ("ST" in nm or "st" in nm):
                continue
            basic = mv_map.get(ts_code, {})
            total_mv = basic.get("total_mv", 0)
            if min_mv > 0 and total_mv < min_mv:
                continue

            elg_buy = _safe_float(r.get("buy_elg_amount"))
            lg_buy = _safe_float(r.get("buy_lg_amount"))
            md_buy = _safe_float(r.get("buy_md_amount"))
            sm_buy = _safe_float(r.get("buy_sm_amount"))
            total_buy = elg_buy + lg_buy + md_buy + sm_buy
            if min_amt > 0 and total_buy < min_amt:
                continue
            ratio = (elg_buy / total_buy * 100) if total_buy > 0 else 0
            if ratio > th_main:
                all_candidates.append({
                    "ts_code": ts_code,
                    "name": nm,
                    "signal_date": scan_date,
                    "close_at_signal": basic.get("close", 0),
                    "ratio": round(ratio, 1),
                })

    logger.info(
        f"[双紫回测] 条件1过滤完成: {len(all_candidates)} 个候选，"
        f"涉及 {len({c['ts_code'] for c in all_candidates})} 只股票"
    )

    status_cache: dict[str, dict[str, dict]] = {}
    unique_codes = list({c["ts_code"] for c in all_candidates})

    for idx, ts_code in enumerate(unique_codes):
        try:
            full_status = compute_zhuli_status(ts_code, days=lookback_days + hold_days + 30)
            date_map = {s["date"]: s for s in full_status}
            status_cache[ts_code] = date_map
        except Exception:
            status_cache[ts_code] = {}
        if (idx + 1) % 20 == 0:
            logger.info(f"[双紫回测] 主力状态计算进度: {idx+1}/{len(unique_codes)}")

    logger.info(f"[双紫回测] 主力状态计算完成，开始双紫确认...")

    all_signals: list[dict] = []
    date_hit_count: dict[str, int] = {}

    for c in all_candidates:
        date_map = status_cache.get(c["ts_code"], {})
        status_entry = date_map.get(c["signal_date"], {})
        if status_entry.get("短线强势", 0) > 0:
            c["status_value"] = status_entry["短线强势"]
            all_signals.append(c)
            date_hit_count[c["signal_date"]] = date_hit_count.get(c["signal_date"], 0) + 1

    for dr in daily_results:
        dr["hits"] = 0
    daily_results = [
        {"date": d, "hits": date_hit_count.get(d, 0)}
        for d in scan_dates
    ]

    logger.info(f"[双紫回测] 双紫确认: {len(all_candidates)} → {len(all_signals)} 个信号")

    if not all_signals:
        return {
            "summary": {
                "lookback_days": lookback_days, "hold_days": hold_days,
                "scan_dates_count": len(scan_dates), "total_signals": 0,
                "unique_stocks": 0, "avg_return": 0, "median_return": 0,
                "win_rate": 0, "max_return": 0, "min_return": 0,
                "market_avg_return": 0, "excess_return": 0,
            },
            "daily_results": daily_results,
            "signal_details": [],
        }

    logger.info(f"[双紫回测] 共 {len(all_signals)} 个信号，开始获取收益数据...")

    signal_codes = list({s["ts_code"] for s in all_signals})
    start_date = scan_dates[0]
    end_date = trade_dates[-1]

    price_map: dict[str, dict[str, float]] = {}
    batch_size = 50
    for i in range(0, len(signal_codes), batch_size):
        batch = signal_codes[i:i + batch_size]
        codes_str = ",".join(batch)
        try:
            df_daily = pro.daily(
                ts_code=codes_str,
                start_date=start_date,
                end_date=end_date,
                fields="ts_code,trade_date,close",
            )
            if df_daily is not None and not df_daily.empty:
                for _, row in df_daily.iterrows():
                    key = row["ts_code"]
                    if key not in price_map:
                        price_map[key] = {}
                    price_map[key][row["trade_date"]] = float(row["close"])
        except Exception as e:
            logger.warning(f"[双紫回测] 批量价格获取失败: {e}")

    enriched_signals = []
    returns_dp = []
    for sig in all_signals:
        ts_code = sig["ts_code"]
        sig_date = sig["signal_date"]
        prices = price_map.get(ts_code, {})

        sig_idx = trade_dates.index(sig_date) if sig_date in trade_dates else -1
        if sig_idx < 0:
            continue

        end_idx = min(sig_idx + hold_days, len(trade_dates) - 1)
        end_date_str = trade_dates[end_idx]

        p_start = prices.get(sig_date)
        p_end = prices.get(end_date_str)

        if p_start and p_end and p_start > 0:
            ret = (p_end - p_start) / p_start * 100
            sig["return_pct"] = round(ret, 2)
            sig["end_date"] = end_date_str
            sig["close_at_end"] = p_end
            enriched_signals.append(sig)
            returns_dp.append(ret)

    market_returns = []
    try:
        for scan_date in scan_dates:
            sig_idx = trade_dates.index(scan_date) if scan_date in trade_dates else -1
            if sig_idx < 0:
                continue
            end_idx = min(sig_idx + hold_days, len(trade_dates) - 1)
            end_date_str = trade_dates[end_idx]

            idx_data_start = market_data.get_price("000001.SH", days=0)
            idx_start = None
            idx_end = None
            idx_prices = price_map.get("000001.SH", {})
            if not idx_prices:
                try:
                    df_idx = pro.index_daily(
                        ts_code="000001.SH",
                        start_date=start_date,
                        end_date=trade_dates[-1],
                        fields="trade_date,close",
                    )
                    if df_idx is not None and not df_idx.empty:
                        for _, row in df_idx.iterrows():
                            if "000001.SH" not in price_map:
                                price_map["000001.SH"] = {}
                            price_map["000001.SH"][row["trade_date"]] = float(row["close"])
                        idx_prices = price_map.get("000001.SH", {})
                except Exception:
                    pass

            idx_start = idx_prices.get(scan_date)
            idx_end = idx_prices.get(end_date_str)
            if idx_start and idx_end and idx_start > 0:
                market_returns.append((idx_end - idx_start) / idx_start * 100)
    except Exception as e:
        logger.warning(f"[双紫回测] 市场基准计算失败: {e}")

    dp_arr = np.array(returns_dp) if returns_dp else np.array([0.0])
    mkt_arr = np.array(market_returns) if market_returns else np.array([0.0])

    summary = {
        "lookback_days": lookback_days,
        "hold_days": hold_days,
        "scan_dates_count": len(scan_dates),
        "total_signals": len(enriched_signals),
        "unique_stocks": len({s["ts_code"] for s in enriched_signals}),
        "avg_return": round(float(np.mean(dp_arr)), 2),
        "median_return": round(float(np.median(dp_arr)), 2),
        "win_rate": round(float(np.mean(dp_arr > 0) * 100), 1),
        "max_return": round(float(np.max(dp_arr)), 2),
        "min_return": round(float(np.min(dp_arr)), 2),
        "market_avg_return": round(float(np.mean(mkt_arr)), 2),
        "excess_return": round(float(np.mean(dp_arr) - np.mean(mkt_arr)), 2),
    }

    enriched_signals.sort(key=lambda s: s.get("return_pct", 0), reverse=True)

    logger.info(
        f"[双紫回测] 完成: {summary['total_signals']}信号, "
        f"均涨{summary['avg_return']}%, 胜率{summary['win_rate']}%, "
        f"超额{summary['excess_return']}%"
    )

    return {
        "summary": summary,
        "daily_results": daily_results,
        "signal_details": enriched_signals[:200],
    }


# ── LLM 分析 ────────────────────────────────────────


async def _generate_alert_analysis(alert: dict) -> str:
    from app.engine.llm import LLMMessage, get_llm_client

    _TYPE_CN = {
        "inflow": "主力大额流入", "outflow": "主力大额流出",
        "reversal_inflow": "资金流反转（转入）", "reversal_outflow": "资金流反转（转出）",
        "trend_in": "连续流入趋势", "trend_out": "连续流出趋势",
    }
    direction = _TYPE_CN.get(alert["type"], alert["type"])
    trend = alert.get("trend", {})
    trend_lines = ""
    if trend:
        cd = trend.get("consecutive_days", 0)
        parts = []
        if cd > 0:
            parts.append(f"连续{cd}日净流入")
        elif cd < 0:
            parts.append(f"连续{abs(cd)}日净流出")
        if trend.get("accelerating"):
            parts.append("资金加速")
        if trend.get("reversal"):
            parts.append("方向刚反转")
        wk = trend.get("week_total", 0)
        parts.append(f"近5日累计{_fmt_amt(wk)}")
        if trend.get("week_consistent"):
            parts.append("周方向一致")
        trend_lines = f"\n趋势背景: {', '.join(parts)}"
    prompt = (
        f"请用2-3句话简要分析以下主力资金异动:\n"
        f"股票: {alert['name']}（{alert['code']}）\n"
        f"日期: {alert.get('date', '今日')}\n"
        f"预警类型: {direction}\n"
        f"净流入占比 {alert['net_pct']:+.2f}%，净额 {_fmt_amt(alert['net_amount'])}\n"
        f"超大单 {_fmt_amt(alert['super_big'])}，大单 {_fmt_amt(alert['big'])}\n"
        f"股价 {alert['price']:.2f}，涨跌幅 {alert['pct_chg']:+.2f}%"
        f"{trend_lines}\n\n"
        f"分析要点: 结合盘中数据和趋势背景，解读资金流向含义和值得关注的信号。简洁有力，不用标题。"
    )
    try:
        llm = get_llm_client(fast=True)
        resp = await llm.chat(
            messages=[
                LLMMessage(role="system", content="你是专业的A股分析师，擅长解读主力资金动向。"),
                LLMMessage(role="user", content=prompt),
            ],
            temperature=0.5,
            max_tokens=200,
        )
        return resp.content.strip()
    except Exception as e:
        logger.warning(f"[预警] LLM分析失败: {e}")
        return ""


# ── 定时推送入口 ─────────────────────────────────────


async def _save_alert_snapshot(alerts: list[dict], config: dict) -> None:
    """将本次检测的预警结果持久化到 PostgreSQL，供回测回放。"""
    if not alerts:
        return
    try:
        now = _now_cst()
        slot_key = now.strftime("%Y%m%d_%H%M")

        snapshot_entries = []
        for a in alerts:
            snapshot_entries.append({
                "ts": slot_key,
                "datetime": now.strftime("%Y-%m-%d %H:%M"),
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M"),
                "code": a.get("code", ""),
                "ts_code": a.get("ts_code", ""),
                "name": a.get("name", ""),
                "type": a.get("type", ""),
                "level": a.get("level", ""),
                "price": a.get("price", 0),
                "pct_chg": a.get("pct_chg", 0),
                "net_pct": a.get("net_pct", 0),
                "net_amount": a.get("net_amount", 0),
                "trend": a.get("trend", {}),
                "dp": a.get("dp", {}),
            })

        history: list[dict] = await _mem_module.memory_store.get_skill(
            DEFAULT_USER_ID, "stock_alert", "alert_snapshots"
        ) or []

        history.extend(snapshot_entries)

        cutoff = (now - timedelta(days=14)).strftime("%Y-%m-%d")
        history = [s for s in history if s.get("date", "") >= cutoff]

        await _mem_module.memory_store.set_skill(
            DEFAULT_USER_ID, "stock_alert", "alert_snapshots", history
        )
        logger.info(f"[预警快照] 存储 {len(snapshot_entries)} 条 slot={slot_key}，总计 {len(history)} 条")
    except Exception as e:
        logger.warning(f"[预警快照] 存储失败: {e}")


async def run_scheduled_check() -> list[dict]:
    """供 scheduler 调用: 检测持仓预警并返回触发列表。

    所有数据通过 market_data 统一接口获取。
    """
    global _last_alert_check

    watchlist = await get_watchlist()
    if not watchlist:
        return []

    config = await get_alert_config()
    interval = config.get("check_interval", 30)

    if _last_alert_check is None:
        stored = await _mem_module.memory_store.get_skill(
            "default_user", "stock_alert", "_last_alert_check",
        )
        if stored:
            try:
                _last_alert_check = datetime.fromisoformat(stored)
            except (ValueError, TypeError):
                pass

    if _last_alert_check:
        elapsed = (_now_cst() - _last_alert_check).total_seconds() / 60
        if elapsed < interval - 1:
            return []

    loop = asyncio.get_event_loop()

    if config.get("dp_enabled", True):
        for item in watchlist:
            try:
                mf_today = market_data.get_main_force(item["code"], days=0)
                if mf_today:
                    slot = _dp_slot(config.get("check_interval", 30))
                    dp = compute_double_purple(mf_today[0], config)
                    _rolling_cache[f"dp_today:{item['code']}"] = {
                        "data": dp, "source": "market_data",
                        "slot": slot, "ts": _time_mod.time(),
                    }
            except Exception:
                pass

    _last_alert_check = _now_cst()
    try:
        await _mem_module.memory_store.save_skill(
            "default_user", "stock_alert", "_last_alert_check",
            _last_alert_check.isoformat(),
        )
    except Exception:
        pass

    alerts = await loop.run_in_executor(None, _evaluate_alerts, config, watchlist)

    await _save_alert_snapshot(alerts, config)

    return alerts


def format_push_message(alerts: list[dict], ts_label: str | None = None) -> str:
    if ts_label is None:
        ts_label = _now_cst().strftime("%m/%d %H:%M")
    lines = [f"🚨 持仓预警（{ts_label}）— 共 {len(alerts)} 条\n"]
    for a in alerts:
        lines.append(_format_alert(a))
        lines.append("")
    return "\n".join(lines).rstrip()


# ── Skill 主类 ───────────────────────────────────────


@skill(
    name="stock_alert",
    description=(
        "主力资金预警（三层指标）— 监控持仓股票资金流向 + 全市场双紫扫描。\n"
        "盘中: 主力净流入/流出超阈值 → 即时预警\n"
        "全盘扫描: 全A股双紫信号筛选（超大单主动买入占比+占总成交额同时超阈值）\n"
        "日频: 连续同向流入/流出、资金加速 → 趋势预警\n"
        "周频: 5日累计资金流方向一致 → 升级预警等级\n"
        "数据来源: 东方财富主力资金（AKShare）"
    ),
    category=SkillCategory.STOCK,
    icon="🚨",
    dashboard=True,
    config_schema={
        "type": "object",
        "properties": {
            "alert_inflow": {
                "type": "boolean",
                "title": "主力流入预警",
                "description": "主力大额净流入时触发预警",
                "default": True,
            },
            "alert_outflow": {
                "type": "boolean",
                "title": "主力流出预警",
                "description": "主力大额净流出时触发预警",
                "default": True,
            },
            "check_interval": {
                "type": "integer",
                "title": "检测频率（分钟）",
                "description": "盘中自动检测间隔",
                "default": 30,
                "enum": [15, 30, 60],
            },
            "inflow_threshold": {
                "type": "number",
                "title": "流入阈值（%）",
                "description": "主力净流入占比超过此值触发",
                "default": 8.0,
            },
            "outflow_threshold": {
                "type": "number",
                "title": "流出阈值（%）",
                "description": "主力净流出占比低于此值触发（负数）",
                "default": -8.0,
            },
            "push_target": {
                "type": "string",
                "title": "推送目标",
                "description": "预警推送发送到哪里（private=私聊，或飞书群 chat_id）",
                "default": "private",
            },
            "dp_enabled": {
                "type": "boolean",
                "title": "双紫信号预警",
                "description": "超大单主动买入占比 + 占总成交额同时超阈值时触发",
                "default": True,
            },
            "dp_elg_buy_ratio": {
                "type": "number",
                "title": "双紫-超大单买入占比阈值（%）",
                "description": "超大单买入 / (买入+卖出) 超过此值 → 主力状态紫柱",
                "default": 70.0,
            },
            "dp_main_turnover_ratio": {
                "type": "number",
                "title": "双紫-超大单占总成交阈值（%）",
                "description": "超大单买入 / 总成交额 超过此值 → 主力动向紫柱",
                "default": 20.0,
            },
            "push_daily_alert": {
                "type": "boolean",
                "title": "日频追踪推送",
                "description": "开盘时推送持仓日线预警",
                "default": True,
            },
            "push_intraday_alert": {
                "type": "boolean",
                "title": "盘中追踪推送",
                "description": "盘中30min频率推送持仓预警",
                "default": True,
            },
            "push_market_scan_close": {
                "type": "boolean",
                "title": "收盘全市场扫描推送",
                "description": "15:30收盘后推送全市场双紫扫描结果",
                "default": True,
            },
            "push_market_scan_intraday": {
                "type": "boolean",
                "title": "盘中全市场扫描推送",
                "description": "14:30推送盘中全市场双紫预扫结果",
                "default": False,
            },
            "scan_min_market_cap": {
                "type": "number",
                "title": "扫描-最低总市值（亿）",
                "description": "全市场扫描时排除总市值低于此值的股票",
                "default": 100.0,
            },
            "scan_min_amount": {
                "type": "number",
                "title": "扫描-最低成交额（万）",
                "description": "全市场扫描时排除当日总成交额低于此值的股票",
                "default": 5000.0,
            },
            "scan_exclude_st": {
                "type": "boolean",
                "title": "扫描-排除ST",
                "description": "全市场扫描时排除ST/*ST股票",
                "default": True,
            },
        },
    },
    parameters_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["check", "backtest", "configure", "scan"],
                "description": (
                    "操作: check=检查当前预警, "
                    "backtest=回测过去7天, "
                    "configure=查看/修改设置, "
                    "scan=全市场双紫扫描（全A股筛选双紫信号）"
                ),
            },
            "setting": {
                "type": "string",
                "description": (
                    "configure 时的设置项，如: "
                    "'inflow_on', 'inflow_off', 'outflow_on', 'outflow_off', "
                    "'interval_15', 'interval_30', 'interval_60'"
                ),
            },
        },
        "required": ["action"],
    },
)
class StockAlertSkill(Skill):

    async def execute(self, context: SkillContext, **kwargs: Any) -> SkillResult:
        action = self._normalize_action(kwargs.get("action", "check"))
        user_id = context.user_id or DEFAULT_USER_ID

        if action == "check":
            return await self._check(user_id)
        elif action == "backtest":
            return await self._backtest(user_id)
        elif action == "configure":
            return await self._configure(user_id, kwargs)
        elif action == "scan":
            return await self._scan(user_id)
        return SkillResult.fail(f"未知操作: {action}")

    @staticmethod
    def _normalize_action(action: str) -> str:
        a = action.lower().replace("-", "_").replace(" ", "_")
        if any(k in a for k in ("scan", "扫描", "全盘", "全市场", "双紫")):
            return "scan"
        if any(k in a for k in ("check", "检查", "检测", "预警", "资金")):
            return "check"
        if any(k in a for k in ("backtest", "back_test", "回测", "历史", "过去")):
            return "backtest"
        if any(k in a for k in ("config", "set", "设置", "修改", "开启", "关闭", "频率")):
            return "configure"
        return action

    async def _scan(self, user_id: str) -> SkillResult:
        config = await get_alert_config()
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, scan_market_dp, config)
        except Exception as e:
            return SkillResult.fail(f"全市场扫描失败: {e}")

        hits = result.get("hits", [])
        trade_date = result.get("trade_date", "")
        total = result.get("total_scanned", 0)

        if not hits:
            return SkillResult(
                success=True,
                summary=f"📊 全市场双紫扫描 ({trade_date})\n扫描 {total} 只，未发现双紫信号。",
            )

        summary = format_scan_message(result)
        return SkillResult(success=True, summary=summary)

    async def _check(self, user_id: str) -> SkillResult:
        watchlist = await get_watchlist()
        if not watchlist:
            return SkillResult(
                success=True,
                summary="持仓列表为空，无法检测预警。\n请先通过持仓技能添加关注股票。",
            )

        config = await get_alert_config()
        loop = asyncio.get_event_loop()

        alerts = await loop.run_in_executor(None, _evaluate_alerts, config, watchlist)
        now = _now_cst().strftime("%m/%d %H:%M")

        if not alerts:
            return SkillResult(
                success=True,
                summary=(
                    f"🔍 主力资金检测（{now}）\n\n"
                    f"已检测 {len(watchlist)} 只持仓股票，当前无预警触发。\n"
                    f"流入阈值: >{config['inflow_threshold']}%  "
                    f"流出阈值: <{config['outflow_threshold']}%"
                ),
            )

        lines = [f"🚨 主力资金预警（{now}）— 共 {len(alerts)} 条\n"]
        for a in alerts:
            lines.append(_format_alert(a))
            lines.append("")

        return SkillResult(success=True, data={"alerts": alerts}, summary="\n".join(lines))

    async def _backtest(self, user_id: str) -> SkillResult:
        watchlist = await get_watchlist()
        if not watchlist:
            return SkillResult(
                success=True,
                summary="持仓列表为空，无法回测。\n请先通过持仓技能添加关注股票。",
            )

        config = await get_alert_config()
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, _run_backtest, watchlist, config, 7)
        except Exception as e:
            logger.error(f"[预警回测] 失败: {e}", exc_info=True)
            return SkillResult.fail(f"回测失败: {e}")

        lines = [
            f"📊 预警回测 · 过去 {result['days']} 天（三层指标）\n",
            f"检测股票: {result['stocks_checked']} 只",
            f"预警总数: {result['total_alerts']} 条",
            f"  🔴 流入预警: {result.get('inflow_alerts', 0)} 条",
            f"  🟢 流出预警: {result.get('outflow_alerts', 0)} 条",
            f"  📈📉 趋势预警: {result.get('trend_alerts', 0)} 条",
            f"  ⚡ 反转预警: {result.get('reversal_alerts', 0)} 条",
        ]

        example = result.get("example")
        ui_card = None

        if example:
            lines.append("\n━━━ 最显著预警示例 ━━━")
            lines.append(_format_alert(example))
            lines.append(f"日期: {example.get('date', '—')}")

            analysis = await _generate_alert_analysis(example)
            if analysis:
                lines.append(f"\n📝 分析\n{analysis}")

            if result.get("chart_b64"):
                ui_card = {
                    "type": "stock_alert_backtest",
                    "image": f"data:image/png;base64,{result['chart_b64']}",
                    "title": f"{example['name']} 预警回测",
                }
        else:
            lines.append("\n过去7天内无预警触发。")

        return SkillResult(
            success=True, data=result, summary="\n".join(lines), ui_card=ui_card,
        )

    async def _configure(self, user_id: str, kwargs: dict) -> SkillResult:
        config = await get_alert_config()
        changed: list[str] = []

        setting = str(kwargs.get("setting", "")).lower().strip()

        _SETTING_MAP = {
            "inflow_on": ("alert_inflow", True),
            "inflow_off": ("alert_inflow", False),
            "outflow_on": ("alert_outflow", True),
            "outflow_off": ("alert_outflow", False),
            "interval_15": ("check_interval", 15),
            "interval_30": ("check_interval", 30),
            "interval_60": ("check_interval", 60),
        }

        if setting in _SETTING_MAP:
            key, val = _SETTING_MAP[setting]
            config[key] = val
            if key == "alert_inflow":
                changed.append(f"主力流入预警: {'开启' if val else '关闭'}")
            elif key == "alert_outflow":
                changed.append(f"主力流出预警: {'开启' if val else '关闭'}")
            elif key == "check_interval":
                changed.append(f"检测频率: 每 {val} 分钟")

        if not changed:
            inflow_s = "✅" if config["alert_inflow"] else "❌"
            outflow_s = "✅" if config["alert_outflow"] else "❌"
            return SkillResult(
                success=True,
                summary=(
                    f"当前预警配置:\n"
                    f"  {inflow_s} 主力流入预警\n"
                    f"  {outflow_s} 主力流出预警\n"
                    f"  检测频率: 每 {config['check_interval']} 分钟\n"
                    f"  流入阈值: >{config['inflow_threshold']}%\n"
                    f"  流出阈值: <{config['outflow_threshold']}%"
                ),
            )

        await _mem_module.memory_store.set_skill(user_id, "stock_alert", ALERT_CONFIG_KEY, config)
        return SkillResult(
            success=True,
            summary="预警配置已更新:\n" + "\n".join(f"  ✓ {c}" for c in changed),
        )
