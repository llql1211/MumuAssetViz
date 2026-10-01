"""按天聚合结果的缓存。

缓存键 = (xlsx 路径, mtime_ns, 文件大小, version)，
任一不一致 → 缓存失效，重算并覆盖。
version 为解析/聚合逻辑版本号，逻辑变更时手工 +1。
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path

import pandas as pd

from app.paths import CACHE_FILE

logger = logging.getLogger(__name__)

# 解析/聚合逻辑版本号；变更逻辑时 +1，强制重建缓存
CACHE_VERSION = 4


def _file_key(path: Path) -> dict:
    st = path.stat()
    return {
        "xlsx": str(path),
        "mtime_ns": st.st_mtime_ns,
        "size": st.st_size,
        "version": CACHE_VERSION,
    }


def _read_payload():
    with open(CACHE_FILE, "rb") as f:
        return pickle.load(f)


def cache_valid(path: Path) -> bool:
    if not CACHE_FILE.exists():
        return False
    try:
        payload = _read_payload()
    except Exception:
        logger.warning("缓存损坏，忽略并重建")
        return False
    return payload.get("meta") == _file_key(path)


def load_cache(path: Path) -> tuple[pd.DataFrame, dict] | None:
    """命中返回 (按天序列, 每日支出TopN)，未命中返回 None。"""
    if not cache_valid(path):
        return None
    payload = _read_payload()
    logger.info("命中缓存：%s", path.name)
    return payload["data"], payload.get("daily_top", {})


def save_cache(path: Path, daily: pd.DataFrame, daily_top: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": _file_key(path), "data": daily, "daily_top": daily_top}
    with open(CACHE_FILE, "wb") as f:
        pickle.dump(payload, f)
    logger.info("已写入缓存：%s", CACHE_FILE)
