"""A 股股票分析 Skill — K 线 + 主力状态 + 主力动向三面板图。"""

import base64
import io
from datetime import datetime, timedelta
from typing import Any

import mplfinance as mpf
import pandas as pd
import tushare as ts
from loguru import logger

from app.config import settings
from app.skills.base import Skill, SkillCategory, SkillContext, SkillResult, skill

import matplotlib
matplotlib.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Sans CJK", "WenQuanYi Micro Hei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

_MARKET_COLORS = mpf.make_marketcolors(
    up="#ef5350", down="#26a69a",
    edge="inherit", wick="inherit",
    volume={"up": "#ef5350", "down": "#26a69a"},
)
_CHART_STYLE = mpf.make_mpf_style(
    base_mpf_style="nightclouds",
    marketcolors=_MARKET_COLORS,
    facecolor="#0d0d0f", edgecolor="#1a1a1e",
    figcolor="#0d0d0f", gridcolor="#1a1a1e", gridstyle="--",
    y_on_right=True,
    rc={"font.size": 9, "axes.labelcolor": "#888",
        "xtick.color": "#666", "ytick.color": "#666"},
)

PERIOD_MAP = {
    "daily":   ("日K", "daily", 120),
    "weekly":  ("周K", "weekly", 300),
    "monthly": ("月K", "monthly", 800),
}

_name_cache: dict[str, str] = {}
_code_cache: dict[str, str] = {}
_map_loaded_at: float = 0


def _get_pro():
    """保留兼容，内部统一走 market_data。"""
    from app.data import market_data
    return market_data._get_pro()


def _load_stock_map():
    """加载 ts_code <-> name 双向映射（通过统一数据层，内置 1h 缓存）"""
    import time as _time
    global _map_loaded_at
    if _name_cache and (_time.time() - _map_loaded_at < 3600):
        return
    try:
        from app.data import market_data
        df = market_data.get_stock_list()
        for _, row in df.iterrows():
            _name_cache[row["ts_code"]] = row["name"]
            _code_cache[row["name"]] = row["ts_code"]
        _map_loaded_at = _time.time()
    except Exception as e:
        logger.warning(f"加载股票列表失败: {e}")


def _resolve(symbol: str) -> tuple[str, str]:
    """将输入解析为 (ts_code 如 000001.SZ, 名称)"""
    _load_stock_map()
    s = symbol.strip()

    if s in _code_cache:
        return _code_cache[s], s

    for name, code in _code_cache.items():
        if s in name or name in s:
            return code, name

    clean = s.replace(".SZ", "").replace(".SS", "").replace(".SH", "").replace("SZ", "").replace("SH", "").replace("sz", "").replace("sh", "")
    if clean.isdigit() and len(clean) == 6:
        if clean.startswith("6"):
            ts_code = f"{clean}.SH"
        else:
            ts_code = f"{clean}.SZ"
        name = _name_cache.get(ts_code, clean)
        return ts_code, name

    return "", ""


@skill(
    name="stock_chart",
    description=(
        "查询 A 股股票行情并生成分析图（K线 + 主力状态 + 主力动向三面板）。"
        "输入股票代码（如 000001）或股票名称（如 平安银行）。"
        "适用于用户问'帮我看看XX'、'查看XX股票'、'XX怎么样'、'XX的走势'、'XX的K线'等场景。"
    ),
    category=SkillCategory.STOCK,
    icon="📈",
    config_schema={
        "type": "object",
        "properties": {
            "default_period": {
                "type": "string", "title": "默认周期",
                "default": "daily", "enum": ["daily", "weekly", "monthly"],
            },
        },
    },
    parameters_schema={
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "股票代码（如 000001、600519）或股票名称（如 平安银行、贵州茅台）",
            },
            "period": {
                "type": "string", "enum": ["daily", "weekly", "monthly"],
                "description": "K 线周期: daily=日K, weekly=周K, monthly=月K",
            },
            "mode": {
                "type": "string", "enum": ["analysis", "kline"],
                "description": "图表模式: analysis=K线+主力状态+主力动向(默认), kline=纯K线图",
            },
        },
        "required": ["symbol"],
    },
)
class StockChartSkill(Skill):

    async def on_load(self) -> None:
        if not settings.tushare_token:
            logger.warning("TUSHARE_TOKEN 未配置，stock_chart 将不可用")

    async def execute(self, context: SkillContext, **kwargs: Any) -> SkillResult:
        if not settings.tushare_token:
            return SkillResult.fail("TUSHARE_TOKEN 未配置")

        symbol = (
            kwargs.get("symbol") or kwargs.get("stock_name")
            or kwargs.get("stock_code") or kwargs.get("ticker")
            or kwargs.get("code") or kwargs.get("name") or ""
        ).strip()
        period = kwargs.get("period") or self.cfg("default_period", "daily")
        mode = kwargs.get("mode", "analysis")

        if not symbol:
            return SkillResult.fail("请提供股票代码或名称")

        try:
            import asyncio
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: self._do_chart(symbol, period, mode)),
                timeout=45,
            )
            return result
        except asyncio.TimeoutError:
            return SkillResult.fail("查询超时，请稍后再试")
        except Exception as e:
            logger.error(f"股票查询失败: {e}", exc_info=True)
            return SkillResult.fail(f"查询失败: {str(e)}")

    def _do_chart(self, symbol: str, period: str, mode: str) -> SkillResult:
        ts_code, name = _resolve(symbol)
        if not ts_code:
            return SkillResult.fail(f"未找到股票「{symbol}」")

        from app.data import market_data

        period_label, _, lookback_days = PERIOD_MAP.get(period, PERIOD_MAP["daily"])

        records = market_data.get_price(ts_code, days=lookback_days)

        if not records:
            return SkillResult.fail(f"未获取到 {name}({ts_code}) 的行情数据")

        df = pd.DataFrame(records)
        df = df.rename(columns={
            "date": "Date", "open": "Open", "close": "Close",
            "high": "High", "low": "Low", "volume": "Volume",
        })
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        df["Volume"] = df["Volume"] * 100

        if period == "weekly":
            df = df.resample("W").agg({
                "Open": "first", "High": "max", "Low": "min",
                "Close": "last", "Volume": "sum",
            }).dropna()
        elif period == "monthly":
            df = df.resample("ME").agg({
                "Open": "first", "High": "max", "Low": "min",
                "Close": "last", "Volume": "sum",
            }).dropna()

        latest = df.iloc[-1]
        prev_close = df.iloc[-2]["Close"] if len(df) > 1 else latest["Open"]
        change = latest["Close"] - prev_close
        pct = (change / prev_close) * 100 if prev_close else 0
        arrow = "🔴 +" if change >= 0 else "🟢 "

        trade_date = df.index[-1]
        trade_date_str = trade_date.strftime("%Y年%m月%d日")

        code6 = ts_code.split(".")[0]

        if mode == "analysis":
            img_b64, dp_line = self._render_analysis(ts_code, code6, name, period_label)
        else:
            img_b64 = self._render_kline(df, ts_code, name, period_label)
            dp_line = ""

        fixed_text = f"{name} {trade_date_str} 分析图如下"
        if dp_line:
            fixed_text += f"\n{dp_line}"
        summary = f"{fixed_text}\n请原样输出以上内容，不要做任何额外分析或解读。"

        return SkillResult(
            success=True,
            data={"code": ts_code, "name": name, "period": period, "mode": mode},
            summary=summary,
            ui_card={
                "type": "stock_chart",
                "image": f"data:image/png;base64,{img_b64}",
                "title": f"{name} ({ts_code}) {period_label}",
            },
        )

    def _render_analysis(self, ts_code: str, code6: str, name: str, period_label: str) -> tuple[str, str]:
        """3-panel chart + double purple status text."""
        from app.skills.finance.stock_alert import (
            render_push_chart, compute_zhuli_status, _DEFAULTS, _safe_float,
        )
        from app.data import market_data

        mock_alert = {
            "type": "query",
            "code": code6,
            "ts_code": ts_code,
            "name": name,
            "price": 0,
            "pct_chg": 0,
            "net_amount": 0,
            "net_pct": 0,
            "level": "neutral",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
        }

        price_hist = market_data.get_price(ts_code, days=14)
        if price_hist:
            last = price_hist[-1]
            mock_alert["price"] = _safe_float(last.get("close", last.get("price", 0)))
            mock_alert["pct_chg"] = _safe_float(last.get("pct_chg", 0))

        dp_hist = market_data.get_main_force(ts_code, 14)

        try:
            status_data = compute_zhuli_status(ts_code, days=14)
        except Exception:
            status_data = []

        img_b64 = render_push_chart(
            mock_alert, price_hist, dp_hist, chart_days=14, status_data=status_data,
        )

        dp_line = self._build_dp_status(status_data, dp_hist, _DEFAULTS)
        return img_b64, dp_line

    @staticmethod
    def _build_dp_status(
        status_data: list[dict],
        dp_hist: list[dict],
        defaults: dict,
    ) -> str:
        """Determine double/single purple dates within the data window."""
        th_main = defaults.get("dp_main_turnover_ratio", 20.0)

        status_by_date = {d["date"]: d for d in status_data} if status_data else {}
        dp_by_date = {d["date"]: d for d in dp_hist} if dp_hist else {}

        all_dates = sorted(set(list(status_by_date.keys()) + list(dp_by_date.keys())))

        double_dates: list[str] = []
        single_dates: list[str] = []

        for date in all_dates:
            s = status_by_date.get(date, {})
            d = dp_by_date.get(date, {})
            is_status_purple = s.get("color", "") == "#ff00ff"
            is_trend_purple = d.get("main_turnover_ratio", 0) > th_main

            if is_status_purple and is_trend_purple:
                double_dates.append(date)
            elif is_status_purple or is_trend_purple:
                single_dates.append(date)

        def _fmt(dates: list[str]) -> str:
            return ", ".join(
                f"{d[4:6]}/{d[6:]}" if len(d) == 8 else d for d in sorted(dates, reverse=True)
            )

        if double_dates:
            line = f"双紫: {_fmt(double_dates)}"
            if single_dates:
                line += f" ｜ 单紫: {_fmt(single_dates)}"
            return line
        elif single_dates:
            return f"14日内无双紫 ｜ 单紫: {_fmt(single_dates)}"
        else:
            return "14日内未出现双紫"

    def _render_kline(self, df, code, name, period_label) -> str:
        """Pure candlestick K-line chart."""
        import matplotlib.pyplot as plt
        fig, _ = mpf.plot(
            df, type="candle", style=_CHART_STYLE,
            volume=True, mav=(5, 10, 20),
            title=f"\n{name} ({code}) {period_label}",
            figsize=(10, 6), tight_layout=True, returnfig=True,
            scale_width_adjustment=dict(candle=0.8, volume=0.65),
        )
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                    facecolor="#0d0d0f", edgecolor="none")
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.getvalue()).decode("ascii")
