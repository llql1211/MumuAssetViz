"""按天聚合：累计资产 + 每日支出。

从同一份明细同时产出两个量：
- asset   = 每日净额（收入-支出）的 cumsum，起始 0；无账目的天向后继承（ffill）。
- expense = 当日所有负金额的绝对值之和；无账目的天为 0。
"""

from __future__ import annotations

import pandas as pd


def aggregate(detail: pd.DataFrame) -> pd.DataFrame:
    """明细 → 按天序列 DataFrame（索引 date，列 asset/expense）。"""
    if detail.empty:
        return pd.DataFrame(
            {"asset": pd.Series(dtype=float), "expense": pd.Series(dtype=float)}
        )

    # 按天净额（index 统一为 DatetimeIndex，便于 reindex 到连续日期）
    net = detail.groupby(pd.to_datetime(detail["date"]))["amount"].sum()

    # 当日支出 = -负金额求和（正数）
    neg = detail[detail["amount"] < 0]
    expense = -neg.groupby(pd.to_datetime(neg["date"]))["amount"].sum()

    # 累计资产 = 每日净额 cumsum（起始 0）；无账目的天向后继承（ffill）
    full = pd.date_range(net.index.min(), net.index.max(), freq="D")
    cum = net.cumsum()
    asset = cum.reindex(full).ffill().fillna(0.0)
    expense_full = expense.reindex(full).fillna(0.0)

    daily = pd.DataFrame(
        {"asset": asset.astype(float), "expense": expense_full.astype(float)}
    )
    daily.index.name = "date"
    return daily
