"""项目路径的唯一来源：各模块从这里取，避免各自拼路径。

目录约定（ROOT 为仓库根）：

    data/input/     放 xlsx 账单（只保留一个）
    data/archived/  手工归档的旧账单
    data/cache.pkl  解析聚合缓存（可随时删，会自动重建）
    data/output.html 生成的图表页面
    assets/         随仓库提交的静态资源（echarts.min.js），非运行时产物

缓存与页面都只有单文件，直接平铺在 data/ 下，不再各自建子目录。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---- 运行时数据 ----
DATA_DIR = ROOT / "data"
INPUT_DIR = DATA_DIR / "input"

CACHE_FILE = DATA_DIR / "cache.pkl"
OUTPUT_FILE = DATA_DIR / "output.html"

# ---- 随仓库提交的静态资源 ----
ASSETS_DIR = ROOT / "assets"
ECHARTS_JS = ASSETS_DIR / "echarts.min.js"
