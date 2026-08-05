"""入口：input/*.xlsx → 解析 → 聚合 → 缓存 → 生成图表 → 打开浏览器。"""

from __future__ import annotations

import logging
import webbrowser

from aggregate import aggregate
from cache import load_cache, save_cache
from chart import build_html
from parse import find_xlsx, parse_xlsx

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("main")


def main() -> None:
    xlsx = find_xlsx()

    daily = load_cache(xlsx)
    if daily is None:
        detail = parse_xlsx(xlsx)
        daily = aggregate(detail)
        save_cache(xlsx, daily)

    # 摘要
    print(f"文件：{xlsx.name}")
    print(f"天数：{len(daily)}，区间：{daily.index.min().date()} ~ {daily.index.max().date()}")
    print(f"期末资产：{daily['asset'].iloc[-1]:,.2f}")
    print(f"总支出：{daily['expense'].sum():,.2f}")

    # 生成图表并打开
    html_path = build_html(daily)
    webbrowser.open(html_path.absolute().as_uri())
    logger.info("浏览器已打开，如果未弹出请手动打开：%s", html_path)


if __name__ == "__main__":
    main()
