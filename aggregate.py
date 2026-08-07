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


def build_daily_top_expenses(detail: pd.DataFrame, n: int = 3) -> dict[str, list[dict]]:
    """按天取支出金额 Top N 明细，供悬停提示框展示。

    只统计 type=="支出" 的行，按 |amount| 从大到小取前 n 条；
    同一天不足 n 条时有多少返回多少。

    返回：{"2021-02-12": [{"a": 4300.0, "c1": "购物", "c2": "数码", "n": "手机"}, ...], ...}
    - a   支出金额（正数）
    - c1  一级分类，c2 二级分类，n 备注（可能为空字符串）
    - key 为 "%Y-%m-%d"，与图表横轴日期对齐。
    """
    if detail.empty:
        return {}
    expenses = detail[detail["type"] == "支出"].copy()
    expenses["a"] = -expenses["amount"].astype(float)  # 支出为负 → 取正
    result: dict[str, list[dict]] = {}
    for date, group in expenses.groupby("date"):
        top = group.nlargest(n, "a")
        items = []
        for _, row in top.iterrows():
            items.append(
                {
                    "a": round(float(row["a"]), 2),
                    "c1": str(row.get("cat1", "") or ""),
                    "c2": str(row.get("cat2", "") or ""),
                    "n": str(row.get("note", "") or ""),
                }
            )
        result[date.strftime("%Y-%m-%d")] = items
    return result
