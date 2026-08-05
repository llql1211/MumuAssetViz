"""输入查找与 xlsx 解析。

只关心 时间/类型/金额 三列，其余忽略。
解析产物是逐条明细（不合并），聚合逻辑在 aggregate.py。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

INPUT_DIR = Path(__file__).parent / "input"

# 仅 收入/支出 计入资产；"转账" 忽略；其他类型告警并忽略
COUNTED_TYPES = {"收入", "支出"}
IGNORED_TYPES = {"转账"}


def find_xlsx(input_dir: Path = INPUT_DIR) -> Path:
    """在 input_dir 下查找唯一的 xlsx 文件。

    多个 → 报错提示删除到只剩一个；0 个 → 报错。
    """
    files = sorted(input_dir.glob("*.xlsx"))
    if not files:
        raise FileNotFoundError(f"input/ 下没有找到 xlsx 文件，请放入账单文件：{input_dir}")
    if len(files) > 1:
        names = "\n  ".join(f.name for f in files)
        raise ValueError(
            f"input/ 下找到 {len(files)} 个 xlsx 文件，请手动删除到只剩一个。\n"
            f"当前文件：\n  {names}"
        )
    return files[0]


def parse_xlsx(path: Path) -> pd.DataFrame:
    """读取 xlsx，返回逐条明细 DataFrame（列：date, type, amount）。

    - 时间只取日期部分（"2021-02-12 20:05" → date）；
    - 类型 收入/支出 计入，转账跳过，其他类型告警并跳过；
    - 金额转 float，支出为负；无效值告警并跳过该行。
    """
    df = pd.read_excel(path, engine="openpyxl")
    missing = {"时间", "类型", "金额"} - set(df.columns)
    if missing:
        raise ValueError(f"xlsx 缺少必需列：{sorted(missing)}")

    raw = df[["时间", "类型", "金额"]].copy()

    # 时间 → date
    dates = pd.to_datetime(raw["时间"], errors="coerce")
    bad_time = dates.isna()
    if bad_time.any():
        logger.warning("跳过 %d 行：时间无法解析", int(bad_time.sum()))

    # 类型清洗：去空白；只保留 收入/支出
    types = raw["类型"].astype(str).str.strip()
    unknown = ~types.isin(COUNTED_TYPES | IGNORED_TYPES)
    if unknown.any():
        logger.warning("跳过 %d 行：未知类型 %s", int(unknown.sum()), sorted(types[unknown].unique()))

    # 金额转 float：先清理千分位逗号、货币符号、空白
    # （否则 "4,549.00" 这类文本单元格会被 to_numeric 判成 NaN 而丢弃）
    amounts = pd.to_numeric(
        raw["金额"].astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("¥", "", regex=False)
        .str.replace("￥", "", regex=False)
        .str.strip(),
        errors="coerce",
    )
    bad_amount = amounts.isna()
    if bad_amount.any():
        logger.warning("跳过 %d 行：金额无效", int(bad_amount.sum()))

    keep = (~bad_time) & (~unknown) & (~bad_amount) & types.isin(COUNTED_TYPES)
    detail = pd.DataFrame(
        {
            "date": dates[keep].dt.date,
            "type": types[keep],
            "amount": amounts[keep].astype(float),
        }
    )
    # 防御性：显式按日期排序，不依赖文件内的排列顺序
    return detail.sort_values("date").reset_index(drop=True)
