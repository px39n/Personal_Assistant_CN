"""统一金融数据接口 — 面向业务语义，自动融合历史+实时。

所有 skill / scheduler / dashboard 通过此模块获取数据，不直接调用第三方库。

公共 API（调用方只用这些）：
    get_price(code, days, freq)      价格 / K线
    get_fund_flow(code, days)        资金流（主力净流入）
    get_main_force(code, days)       主力分单（超大单/大单，双紫用）
    get_main_force_market(date)      全市场分单（扫描用）
    get_index()                      大盘指数快照
    get_stock_list()                 股票列表
    get_trade_cal(year)              交易日历
    is_trading_day(date)             是否交易日
    resolve_stock(query)             名称/代码解析
    get_suspend(...)                 停复牌
    get_daily_basic(...)             日线基础指标（换手率/市值）
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import tushare as ts
from loguru import logger

from app.config import settings

_CST = timezone(timedelta(hours=8))
_pro: ts.pro_api | None = None


def _now_cst() -> datetime:
    return datetime.now(_CST)


def _get_pro() -> ts.pro_api:
    global _pro
    if _pro is None:
        _pro = ts.pro_api(settings.tushare_token)
    return _pro


def _ts_code_to_pure(ts_code: str) -> str:
    return ts_code.split(".")[0]


def _ts_code_to_em_secid(ts_code: str) -> str:
    code, market = ts_code.split(".")
    return f"{'1' if market == 'SH' else '0'}.{code}"


def _ts_code_to_ak_market(ts_code: str) -> str:
    return ts_code.split(".")[1].lower()


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  缓存 — 两层：短期 TTL + 日频滚动
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_cache: dict[str, tuple[float, Any]] = {}
_rolling: dict[str, dict] = {}


def _get_cached(key: str, ttl: float) -> Any | None:
    if key in _cache:
        ts_cached, val = _cache[key]
        if time.time() - ts_cached < ttl:
            return val
    return None


def _set_cached(key: str, val: Any) -> None:
    _cache[key] = (time.time(), val)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  内部数据源（全部 private，调用方不应直接使用）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


_JSONP_RE = re.compile(r"^\w+\((.+)\);?\s*$", re.DOTALL)


def _parse_em_response(text: str) -> dict:
    """解析东方财富响应，兼容纯 JSON 和 JSONP 两种格式。"""
    text = text.strip().lstrip("\ufeff")
    m = _JSONP_RE.match(text)
    if m:
        text = m.group(1)
    return json.loads(text)


def _fetch_em_realtime_single(ts_code: str) -> dict | None:
    """东方财富 push2 实时分单 + 价格（走代理或直连）。"""
    import requests

    secid = _ts_code_to_em_secid(ts_code)
    fields = "f43,f44,f45,f46,f47,f48,f137,f138,f139,f140,f141,f142,f143,f144,f145,f146,f147,f148,f149"
    em_url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields={fields}"

    try:
        if settings.em_proxy_url:
            r = requests.get(settings.em_proxy_url, params={"url": em_url}, timeout=10)
        else:
            r = requests.get(em_url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}, timeout=6)

        data = _parse_em_response(r.text).get("data", {})
        if not data:
            return None

        def _v(key: str) -> float:
            v = data.get(key, 0)
            return v / 100 if isinstance(v, int) and key in ("f43", "f44", "f45", "f46") else _safe_float(v)

        return {
            "price": _v("f43"), "high": _v("f44"), "low": _v("f45"), "open": _v("f46"),
            "volume": _safe_float(data.get("f47")), "amount": _safe_float(data.get("f48")),
            "net_main": _safe_float(data.get("f137")),
            "buy_elg": _safe_float(data.get("f138")), "sell_elg": _safe_float(data.get("f139")),
            "net_elg": _safe_float(data.get("f146")),
            "buy_lg": _safe_float(data.get("f140")), "sell_lg": _safe_float(data.get("f141")),
            "net_lg": _safe_float(data.get("f147")),
            "buy_md": _safe_float(data.get("f142")), "sell_md": _safe_float(data.get("f143")),
            "net_md": _safe_float(data.get("f148")),
            "buy_sm": _safe_float(data.get("f144")), "sell_sm": _safe_float(data.get("f145")),
            "net_sm": _safe_float(data.get("f149")),
        }
    except Exception as e:
        logger.warning(f"[数据层] EM 实时获取失败 {ts_code}: {e}")
        return None


def _fetch_em_batch(ts_codes: list[str]) -> dict[str, dict]:
    """批量 EM 实时（逐只），带 15s TTL 缓存。"""
    result = {}
    for code in ts_codes:
        key = f"em_rt:{code}"
        cached = _get_cached(key, ttl=15)
        if cached is not None:
            result[code] = cached
            continue
        raw = _fetch_em_realtime_single(code)
        if raw:
            _set_cached(key, raw)
            result[code] = raw
    return result


def _fetch_akshare_spot(ts_codes: list[str]) -> dict[str, dict]:
    """AKShare 全市场快照 → 过滤出需要的股票。"""
    import akshare as ak

    cache_key = "ak_spot_all"
    df = _get_cached(cache_key, ttl=15)
    if df is None:
        for attempt in range(2):
            try:
                df = ak.stock_zh_a_spot_em()
                df["代码"] = df["代码"].astype(str)
                _set_cached(cache_key, df)
                break
            except Exception:
                if attempt == 1:
                    return {}
                time.sleep(1)
    if df is None:
        return {}

    result = {}
    for ts_code in ts_codes:
        row = df[df["代码"] == _ts_code_to_pure(ts_code)]
        if row.empty:
            continue
        r = row.iloc[0]
        result[ts_code] = {
            "price": _safe_float(r.get("最新价")),
            "pct_chg": _safe_float(r.get("涨跌幅")),
            "change": _safe_float(r.get("涨跌额")),
            "volume": _safe_float(r.get("成交量")),
            "amount": _safe_float(r.get("成交额")),
            "open": _safe_float(r.get("今开")),
            "high": _safe_float(r.get("最高")),
            "low": _safe_float(r.get("最低")),
            "pre_close": _safe_float(r.get("昨收")),
            "turnover": _safe_float(r.get("换手率")),
            "total_mv": _safe_float(r.get("总市值")),
            "name": str(r.get("名称", "")),
        }
    return result


def _fetch_tushare_daily(ts_code: str, days: int, fields: str | None = None) -> pd.DataFrame:
    pro = _get_pro()
    end = _now_cst().strftime("%Y%m%d")
    start = (_now_cst() - timedelta(days=days + 30)).strftime("%Y%m%d")
    f = fields or "trade_date,open,high,low,close,vol,amount,pct_chg"
    df = pro.daily(ts_code=ts_code, start_date=start, end_date=end, fields=f)
    if df is None or df.empty:
        return pd.DataFrame()
    return df.sort_values("trade_date").tail(days).reset_index(drop=True)


def _fetch_akshare_fund_flow(ts_code: str) -> pd.DataFrame:
    import akshare as ak
    code = _ts_code_to_pure(ts_code)
    market = _ts_code_to_ak_market(ts_code)
    try:
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        return df if df is not None else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _fetch_tushare_moneyflow(ts_code: str, days: int) -> pd.DataFrame:
    pro = _get_pro()
    end = _now_cst().strftime("%Y%m%d")
    start = (_now_cst() - timedelta(days=days + 30)).strftime("%Y%m%d")
    df = pro.moneyflow(ts_code=ts_code, start_date=start, end_date=end)
    if df is None or df.empty:
        return pd.DataFrame()
    return df.sort_values("trade_date").reset_index(drop=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  公共 API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MarketData:
    """面向业务的统一数据接口。

    days=0  → 实时快照
    days>0  → N 日历史 + 今日实时自动拼接
    """

    # ── 价格 / K线 ─────────────────────────────

    def get_price(
        self, ts_code: str, days: int = 0, freq: str = "daily",
    ) -> list[dict]:
        """获取价格数据。

        days=0:  实时快照 → [{"date": "20260407", "price": 58.98, "pct_chg": 1.08, ...}]
        days=30: 30 日 K 线 + 今日 → [{"date", "open", "high", "low", "close", "volume", "pct_chg"}, ...]
        freq="30min": 分钟 K 线（忽略 days 参数）
        """
        if freq != "daily":
            return self._price_intraday(ts_code, freq)

        if days == 0:
            return self._price_snapshot(ts_code)

        return self._price_daily(ts_code, days)

    def _price_snapshot(self, ts_code: str) -> list[dict]:
        today = _now_cst().strftime("%Y%m%d")
        q = _fetch_akshare_spot([ts_code]).get(ts_code)
        if not q:
            em = _fetch_em_batch([ts_code]).get(ts_code)
            if not em:
                return []
            q = {
                "price": em["price"], "pct_chg": 0, "open": em["open"],
                "high": em["high"], "low": em["low"], "volume": em["volume"],
            }
        return [{
            "date": today, "open": q.get("open", 0), "high": q.get("high", 0),
            "low": q.get("low", 0), "close": q.get("price", 0),
            "volume": q.get("volume", 0), "pct_chg": q.get("pct_chg", 0),
            "price": q.get("price", 0), "pre_close": q.get("pre_close", 0),
            "name": q.get("name", ""),
        }]

    def _price_daily(self, ts_code: str, days: int) -> list[dict]:
        df = _fetch_tushare_daily(ts_code, days)
        records = []
        for _, r in df.iterrows():
            records.append({
                "date": str(r["trade_date"]),
                "open": _safe_float(r.get("open")),
                "high": _safe_float(r.get("high")),
                "low": _safe_float(r.get("low")),
                "close": _safe_float(r.get("close")),
                "volume": _safe_float(r.get("vol")),
                "pct_chg": _safe_float(r.get("pct_chg")),
            })

        today = _now_cst().strftime("%Y%m%d")
        if not records or records[-1]["date"] < today:
            em = _fetch_em_batch([ts_code]).get(ts_code)
            if em and em.get("price", 0) > 0:
                records.append({
                    "date": today,
                    "open": em["open"], "high": em["high"],
                    "low": em["low"], "close": em["price"],
                    "volume": em["volume"],
                    "pct_chg": 0,
                })
        return records

    def _price_intraday(self, ts_code: str, freq: str) -> list[dict]:
        pro = _get_pro()
        df = pro.stk_mins(ts_code=ts_code, freq=freq)
        if df is None or df.empty:
            return []
        df = df.sort_values("trade_time").reset_index(drop=True)
        return [{
            "date": str(r["trade_time"]),
            "open": _safe_float(r.get("open")),
            "high": _safe_float(r.get("high")),
            "low": _safe_float(r.get("low")),
            "close": _safe_float(r.get("close")),
            "volume": _safe_float(r.get("vol")),
        } for _, r in df.iterrows()]

    # ── 资金流（主力净流入） ────────────────────

    def get_fund_flow(self, ts_code: str, days: int = 0) -> list[dict]:
        """获取资金流数据。

        days=0:  今日累计 → [{"date", "net_pct", "net_amount", ...}]
        days=30: 近 30 日 + 今日 → [{...}, ...]

        每条记录: {date, net_pct, net_amount, super_big, big, close, pct_chg}
        """
        if days == 0:
            return self._flow_today(ts_code)

        return self._flow_history(ts_code, days)

    def _flow_today(self, ts_code: str) -> list[dict]:
        em = _fetch_em_batch([ts_code]).get(ts_code)
        if not em:
            return []
        return [self._em_to_flow_record(em, _now_cst().strftime("%Y%m%d"))]

    def _flow_history(self, ts_code: str, days: int) -> list[dict]:
        cache_key = f"flow_hist:{ts_code}"
        cached = _rolling.get(cache_key)
        today = _now_cst().strftime("%Y%m%d")

        if cached and cached.get("last_date", "") >= today:
            recs = cached["records"]
        else:
            df = _fetch_akshare_fund_flow(ts_code)
            if df is None or df.empty:
                recs = cached["records"] if cached else []
            else:
                df["日期"] = pd.to_datetime(df["日期"])
                df = df.sort_values("日期").reset_index(drop=True)
                recs = []
                for _, r in df.iterrows():
                    d = r["日期"].strftime("%Y%m%d") if hasattr(r["日期"], "strftime") else str(r["日期"])[:10].replace("-", "")
                    recs.append({
                        "date": d,
                        "net_pct": _safe_float(r.get("主力净流入-净占比")),
                        "net_amount": _safe_float(r.get("主力净流入-净额")),
                        "super_big": _safe_float(r.get("超大单净流入-净额")),
                        "big": _safe_float(r.get("大单净流入-净额")),
                        "close": _safe_float(r.get("收盘价")),
                        "pct_chg": _safe_float(r.get("涨跌幅")),
                    })
                last_d = recs[-1]["date"] if recs else today
                _rolling[cache_key] = {"records": recs, "last_date": last_d}

        cutoff = (_now_cst() - timedelta(days=days + 10)).strftime("%Y%m%d")
        result = [r for r in recs if r["date"] >= cutoff]

        existing_dates = {r["date"] for r in result}
        if today not in existing_dates:
            em = _fetch_em_batch([ts_code]).get(ts_code)
            if em and em.get("price", 0) > 0:
                result.append(self._em_to_flow_record(em, today))

        return result

    def _em_to_flow_record(self, em: dict, date_str: str) -> dict:
        main_buy = _safe_float(em.get("buy_elg", 0)) + _safe_float(em.get("buy_lg", 0))
        main_sell = _safe_float(em.get("sell_elg", 0)) + _safe_float(em.get("sell_lg", 0))
        main_net = _safe_float(em.get("net_main", 0))
        total = main_buy + main_sell
        return {
            "date": date_str,
            "net_pct": round((main_net / total * 100) if total > 0 else 0, 2),
            "net_amount": main_net,
            "super_big": _safe_float(em.get("buy_elg", 0)) - _safe_float(em.get("sell_elg", 0)),
            "big": _safe_float(em.get("buy_lg", 0)) - _safe_float(em.get("sell_lg", 0)),
            "close": _safe_float(em.get("price", 0)),
            "pct_chg": 0,
        }

    # ── 主力分单（双紫用） ─────────────────────

    def get_main_force(self, ts_code: str, days: int = 0) -> list[dict]:
        """获取主力分单数据。

        days=0:  今日累计 → [{"date", "elg_buy", "elg_sell", "main_turnover_ratio", ...}]
        days=30: 近 30 日 + 今日

        每条记录: {date, elg_buy, elg_sell, lg_buy, lg_sell,
                   total_turnover, elg_buy_ratio, main_turnover_ratio}
        """
        if days == 0:
            return self._mf_today(ts_code)
        return self._mf_history(ts_code, days)

    def _mf_today(self, ts_code: str) -> list[dict]:
        em = _fetch_em_batch([ts_code]).get(ts_code)
        if not em:
            return []
        return [self._em_to_mf_record(em, _now_cst().strftime("%Y%m%d"))]

    def _mf_history(self, ts_code: str, days: int) -> list[dict]:
        cache_key = f"mf_hist:{ts_code}"
        cached = _rolling.get(cache_key)
        today = _now_cst().strftime("%Y%m%d")

        if cached and cached.get("last_date", "") >= today:
            recs = cached["records"]
        else:
            df = _fetch_tushare_moneyflow(ts_code, max(days, 90))
            if df is None or df.empty:
                recs = cached["records"] if cached else []
            else:
                recs = [self._moneyflow_row_to_record(r) for _, r in df.iterrows()]
                last_d = recs[-1]["date"] if recs else today
                _rolling[cache_key] = {"records": recs, "last_date": last_d}

        cutoff = (_now_cst() - timedelta(days=days + 10)).strftime("%Y%m%d")
        result = [r for r in recs if r["date"] >= cutoff]

        existing_dates = {r["date"] for r in result}
        if today not in existing_dates:
            em = _fetch_em_batch([ts_code]).get(ts_code)
            if em and em.get("price", 0) > 0:
                result.append(self._em_to_mf_record(em, today))

        return result

    @staticmethod
    def _moneyflow_row_to_record(r) -> dict:
        elg_buy = _safe_float(r.get("buy_elg_amount"))
        elg_sell = _safe_float(r.get("sell_elg_amount"))
        lg_buy = _safe_float(r.get("buy_lg_amount"))
        lg_sell = _safe_float(r.get("sell_lg_amount"))
        md_buy = _safe_float(r.get("buy_md_amount"))
        sm_buy = _safe_float(r.get("buy_sm_amount"))
        total = elg_buy + lg_buy + md_buy + sm_buy
        elg_total = elg_buy + elg_sell
        return {
            "date": str(r["trade_date"]),
            "elg_buy": elg_buy, "elg_sell": elg_sell,
            "lg_buy": lg_buy, "lg_sell": lg_sell,
            "total_turnover": total,
            "elg_buy_ratio": round((elg_buy / elg_total * 100) if elg_total > 0 else 0, 2),
            "main_turnover_ratio": round((elg_buy / total * 100) if total > 0 else 0, 2),
        }

    @staticmethod
    def _em_to_mf_record(em: dict, date_str: str) -> dict:
        elg_buy = _safe_float(em.get("buy_elg"))
        elg_sell = _safe_float(em.get("sell_elg"))
        lg_buy = _safe_float(em.get("buy_lg"))
        lg_sell = _safe_float(em.get("sell_lg"))
        md_buy = _safe_float(em.get("buy_md"))
        sm_buy = _safe_float(em.get("buy_sm"))
        total = elg_buy + lg_buy + md_buy + sm_buy
        elg_total = elg_buy + elg_sell
        return {
            "date": date_str,
            "elg_buy": elg_buy, "elg_sell": elg_sell,
            "lg_buy": lg_buy, "lg_sell": lg_sell,
            "total_turnover": total,
            "elg_buy_ratio": round((elg_buy / elg_total * 100) if elg_total > 0 else 0, 2),
            "main_turnover_ratio": round((elg_buy / total * 100) if total > 0 else 0, 2),
        }

    # ── 全市场分单（扫描用） ────────────────────

    def get_main_force_market(self, trade_date: str) -> pd.DataFrame:
        """全市场单日分单数据（Tushare moneyflow）。"""
        pro = _get_pro()
        df = pro.moneyflow(trade_date=trade_date)
        if df is None or df.empty:
            return pd.DataFrame()
        return df

    # ── 指数 ───────────────────────────────────

    _INDEX_WANT = {"000001": "上证", "399001": "深证", "399006": "创业板"}

    def get_index(self) -> list[dict]:
        """大盘指数快照。AKShare 优先，失败走 EM 代理。"""
        cached = _get_cached("index_rt", ttl=15)
        if cached is not None:
            return cached

        result = self._index_akshare() or self._index_em_proxy()
        _set_cached("index_rt", result)
        return result

    def _index_akshare(self) -> list[dict]:
        import akshare as ak
        try:
            df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
            df["代码"] = df["代码"].astype(str)
            return [
                {"code": c, "name": n,
                 "close": _safe_float(df[df["代码"] == c].iloc[0].get("最新价")),
                 "pct_chg": _safe_float(df[df["代码"] == c].iloc[0].get("涨跌幅"))}
                for c, n in self._INDEX_WANT.items()
                if not df[df["代码"] == c].empty
            ]
        except Exception:
            return []

    def _index_em_proxy(self) -> list[dict]:
        import requests
        _SECID = {"1.000001": ("000001", "上证"),
                  "0.399001": ("399001", "深证"),
                  "0.399006": ("399006", "创业板")}
        url = f"https://push2.eastmoney.com/api/qt/ulist.np/get?fields=f2,f3,f12,f14&secids={','.join(_SECID.keys())}"
        try:
            if settings.em_proxy_url:
                r = requests.get(settings.em_proxy_url, params={"url": url}, timeout=10)
            else:
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}, timeout=6)
            items = _parse_em_response(r.text).get("data", {}).get("diff", [])
            result = []
            for item in items:
                code = str(item.get("f12", ""))
                for _, (idx_code, label) in _SECID.items():
                    if code == idx_code:
                        cl = item.get("f2", 0)
                        close = cl / 100 if isinstance(cl, int) and cl > 10000 else cl
                        pc = item.get("f3", 0)
                        pct = pc / 100 if isinstance(pc, int) else pc
                        result.append({"code": idx_code, "name": label, "close": float(close), "pct_chg": float(pct)})
            return result
        except Exception as e:
            logger.warning(f"[数据层] EM 代理指数获取失败: {e}")
            return []

    # ── 参考数据 ────────────────────────────────

    def get_stock_list(self) -> pd.DataFrame:
        cached = _get_cached("stock_list", ttl=3600)
        if cached is not None:
            return cached
        pro = _get_pro()
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name")
        if df is None or df.empty:
            df = pd.DataFrame(columns=["ts_code", "name"])
        _set_cached("stock_list", df)
        return df

    def get_trade_cal(self, year: int | None = None) -> pd.DataFrame:
        if year is None:
            year = _now_cst().year
        cache_key = f"trade_cal_{year}"
        cached = _get_cached(cache_key, ttl=86400)
        if cached is not None:
            return cached
        pro = _get_pro()
        df = pro.trade_cal(exchange="SSE", start_date=f"{year}0101", end_date=f"{year}1231")
        if df is None or df.empty:
            df = pd.DataFrame(columns=["cal_date", "is_open"])
        _set_cached(cache_key, df)
        return df

    def is_trading_day(self, date: datetime | None = None) -> bool:
        if date is None:
            date = _now_cst()
        ds = date.strftime("%Y%m%d")
        cal = self.get_trade_cal(date.year)
        if cal.empty:
            return date.weekday() < 5
        row = cal[cal["cal_date"] == ds]
        if row.empty:
            return date.weekday() < 5
        return int(row.iloc[0]["is_open"]) == 1

    def get_suspend(self, suspend_type: str = "S", days: int = 30) -> pd.DataFrame:
        pro = _get_pro()
        end = _now_cst().strftime("%Y%m%d")
        start = (_now_cst() - timedelta(days=days)).strftime("%Y%m%d")
        df = pro.suspend_d(suspend_type=suspend_type, start_date=start, end_date=end)
        if df is None or df.empty:
            return pd.DataFrame()
        return df

    def get_daily_basic(
        self, ts_code: str | None = None, trade_date: str | None = None,
        days: int = 60, *, fields: str = "trade_date,ts_code,close,turnover_rate,total_mv,circ_mv",
    ) -> pd.DataFrame:
        pro = _get_pro()
        if trade_date:
            df = pro.daily_basic(trade_date=trade_date, fields=fields)
        else:
            end = _now_cst().strftime("%Y%m%d")
            start = (_now_cst() - timedelta(days=days + 30)).strftime("%Y%m%d")
            df = pro.daily_basic(ts_code=ts_code, start_date=start, end_date=end, fields=fields)
        if df is None or df.empty:
            return pd.DataFrame()
        if "trade_date" in df.columns:
            df = df.sort_values("trade_date").reset_index(drop=True)
        return df

    _name_map: dict[str, str] | None = None
    _code_map: dict[str, str] | None = None

    def resolve_stock(self, query: str) -> tuple[str, str] | None:
        if self._name_map is None:
            df = self.get_stock_list()
            self._name_map = dict(zip(df["name"], df["ts_code"]))
            self._code_map = dict(zip(df["ts_code"], df["name"]))
        if query in self._code_map:
            return (query, self._code_map[query])
        pure = query.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
        for ts_code, name in self._code_map.items():
            if _ts_code_to_pure(ts_code) == pure:
                return (ts_code, name)
        if query in self._name_map:
            return (self._name_map[query], query)
        for name, ts_code in self._name_map.items():
            if query in name or name in query:
                return (ts_code, name)
        return None

    # ── 向后兼容（过渡期，将被删除） ──────────────

    def get_realtime_quotes(self, ts_codes: list[str]) -> dict[str, dict]:
        """兼容旧接口 → 内部调 get_price。"""
        result = {}
        for code in ts_codes:
            p = self.get_price(code)
            if p:
                result[code] = p[0]
        return result

    def get_index_realtime(self) -> list[dict]:
        return self.get_index()

    def get_kline_daily(self, ts_code: str, days: int = 60, *, fields: str | None = None) -> pd.DataFrame:
        return _fetch_tushare_daily(ts_code, days, fields)

    def get_moneyflow(self, ts_code: str, days: int = 30) -> pd.DataFrame:
        return _fetch_tushare_moneyflow(ts_code, days)

    def get_moneyflow_market(self, trade_date: str) -> pd.DataFrame:
        return self.get_main_force_market(trade_date)

    def get_fund_flow_akshare(self, ts_code: str) -> pd.DataFrame:
        return _fetch_akshare_fund_flow(ts_code)

    def get_em_realtime(self, ts_code: str) -> dict | None:
        return _fetch_em_realtime_single(ts_code)

    def get_kline_min(self, ts_code: str, freq: str = "30min") -> pd.DataFrame:
        pro = _get_pro()
        df = pro.stk_mins(ts_code=ts_code, freq=freq)
        if df is None or df.empty:
            return pd.DataFrame()
        if "trade_time" in df.columns:
            df = df.sort_values("trade_time").reset_index(drop=True)
        return df


market_data = MarketData()
