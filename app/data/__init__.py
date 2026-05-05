"""统一数据层 — 所有金融数据获取的唯一入口。

使用方法:
    from app.data import market_data

    # 价格（days=0 → 实时快照, days>0 → 历史+今日自动合并）
    snap = market_data.get_price("600487.SH", days=0)
    bars = market_data.get_price("600487.SH", days=30)

    # 资金流 / 主力分单
    flow = market_data.get_fund_flow("600487.SH", days=30)
    mf   = market_data.get_main_force("600487.SH", days=30)

    # 指数
    idx  = market_data.get_index()
"""

from app.data.market import market_data

__all__ = ["market_data"]
