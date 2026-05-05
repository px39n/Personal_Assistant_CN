# 数据层与行情

## 原则

业务代码（Skill、调度器、Dashboard API）应通过 **`app.data.market_data`**（`MarketData` 单例）获取行情与指标，避免散落直连第三方库。

## 主要接口（语义化）

| 方法 | 含义 |
|------|------|
| `get_price(ts_code, days=…)` | K 线 / 价格；`days=0` 偏实时快照 |
| `get_fund_flow(…)` | 资金流向（主力净流入等） |
| `get_main_force(…)` | 分单 / 主力动向（双紫相关） |
| `get_main_force_market(date)` | 全市场分单（扫描用，T-1） |
| `get_index()` | 大盘指数快照 |
| `get_stock_list` / `get_trade_cal` / `get_daily_basic` … | 列表、日历、市值换手等 |

实现细节与缓存策略见源码 **`app/data/market.py`** 及包内 **`app/data/__init__.py`** 说明。

## 外部来源

- **Tushare**：日线、分钟、全市场 moneyflow 等（需 token）。
- **AKShare / 东方财富**：部分实时与列表类数据；服务端直连 EM 可能不稳定，故生产可配置 **`EM_PROXY_URL`**（边缘代理转发 `eastmoney.com`）。

代理子项目见仓库 **`cf-em-proxy/`**。
