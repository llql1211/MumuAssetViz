"""图表构建：PyECharts 生成交互式 HTML 页面。

- 主折线（累计资产）+ 浅色条形（每日支出，独立右轴，锚定横轴上方）
- 悬停竖虚线 / 大圆点 / 文本框（日期 + 资产 + 支出）
- 时间段预设按钮（全部/近1月/3月/6月/1年）+ 自定义日期区间
- 纵轴折叠：异常跳跃段压缩为窄带，axisLabel 反函数还原真实值

实现：手工构建 ECharts option 字典（可完全控制交互细节），
用 PyECharts 的 render_embed() 生成可嵌入的 div+script，再套进自定义模板。
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import timedelta
from pathlib import Path

import pandas as pd
from pyecharts import options as opts
from pyecharts.charts import Bar
from pyecharts.commons.utils import JsCode
from pyecharts.globals import CurrentConfig

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_FILE = OUTPUT_DIR / "chart.html"

# ---- 纵轴折叠参数 ----
Y_FOLD_ENABLED = True
Y_FOLD_K = 5          # |日变化| > k × 中位数 → 视为异常跳跃
Y_FOLD_BAND = 0.06    # 每个折叠段占显示高度的比例

# ---- 时间段预设（相对最新日期的天数）----
PRESET_DAYS = {"1m": 30, "3m": 90, "6m": 180, "1y": 365}

# ---- 颜色 ----
COLOR_ASSET = "#FF6B6B"          # 资产折线：珊瑚红
COLOR_EXPENSE = "rgba(173,216,230,0.55)"  # 支出条形：浅蓝


# ---------------------------------------------------------------------------
# 纵轴折叠
# ---------------------------------------------------------------------------

def build_y_fold(asset: pd.Series) -> dict | None:
    """检测异常跳跃，构建「真实值 → 显示值[0,1]」的分段线性映射。

    返回 dict（含 forward 闭包与折叠段信息），无异常时返回 None。
    """
    changes = asset.diff().abs()
    baseline = changes[changes > 0].median()
    if pd.isna(baseline) or baseline == 0:
        return None
    threshold = Y_FOLD_K * baseline

    spike = changes[changes > threshold]
    if len(spike) == 0:
        return None

    # 收集每个跳跃在纵轴上覆盖的区间
    segments = []
    for pos in spike.index:
        loc = asset.index.get_loc(pos)
        prev = asset.iloc[loc - 1] if loc > 0 else asset.iloc[loc]
        curr = asset.iloc[loc]
        segments.append((min(prev, curr), max(prev, curr)))

    # 排序并合并重叠区间
    segments.sort()
    merged: list[list[float]] = [[float(segments[0][0]), float(segments[0][1])]]
    for lo, hi in segments[1:]:
        if lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([float(lo), float(hi)])

    y_min = float(asset.min())
    y_max = float(asset.max())

    # 正常区间总跨度 = 总范围 − 折叠段跨度
    normal_total = (y_max - y_min) - sum(hi - lo for lo, hi in merged)
    if normal_total <= 0:
        return None  # 全部都是折叠段，放弃

    n_folds = len(merged)
    display_normal = 1.0 - n_folds * Y_FOLD_BAND
    scale = display_normal / normal_total

    ranges = merged  # 已合并、非重叠、升序

    def forward(v: float) -> float:
        """真实值 → 显示值 [0, 1]。"""
        v = max(y_min, min(y_max, float(v)))
        disp = 0.0
        prev_end = y_min
        for lo, hi in ranges:
            if v < lo:                      # 落在本折叠段前的正常区间
                return disp + scale * (v - prev_end)
            if v <= hi:                     # 落在折叠段内 → 压缩到窄带
                frac = (v - lo) / (hi - lo) if hi > lo else 0.0
                return disp + scale * (lo - prev_end) + Y_FOLD_BAND * frac
            disp += scale * (lo - prev_end) + Y_FOLD_BAND
            prev_end = hi
        return disp + scale * (v - prev_end)  # 最后一个折叠段之后的正常区间

    return {
        "ranges": ranges,
        "y_min": y_min,
        "y_max": y_max,
        "scale": scale,
        "band": Y_FOLD_BAND,
        "forward": forward,
    }


def _inverse_js(fold: dict) -> str:
    """生成显示值 → 真实值的 JS 反函数（用于 axisLabel formatter）。"""
    ranges_json = json.dumps(fold["ranges"])
    return f"""
function(value) {{
    if (value === '' || value === null || value === undefined) return '';
    var d = Number(value);
    var folds = {ranges_json};
    var yMin = {fold["y_min"]};
    var scale = {fold["scale"]};
    var band = {fold["band"]};
    var acc = 0.0, prevEnd = yMin;
    for (var i = 0; i < folds.length; i++) {{
        var lo = folds[i][0], hi = folds[i][1];
        var normalLen = lo - prevEnd;
        if (d <= acc + scale * normalLen) {{
            return prevEnd + (d - acc) / scale;
        }}
        acc += scale * normalLen;
        if (d <= acc + band) {{
            var frac = (d - acc) / band;
            return lo + frac * (hi - lo);
        }}
        acc += band;
        prevEnd = hi;
    }}
    return prevEnd + (d - acc) / scale;
}}
"""


# ---------------------------------------------------------------------------
# echarts.min.js 本地化
# ---------------------------------------------------------------------------

def _ensure_echarts_js() -> str:
    """确保本地有 echarts.min.js，返回页面引用路径。

    依次尝试多个源下载到 output/ 下（带超时）；全部失败则回退到首个
    CDN 地址（页面内会显示加载失败提示）。首次成功后离线可用。
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)  # 必须先建目录再下载
    local = OUTPUT_DIR / "echarts.min.js"
    if local.exists() and local.stat().st_size > 100_000:
        return "echarts.min.js"

    candidates = [
        CurrentConfig.ONLINE_HOST + "echarts.min.js",
        "https://cdn.bootcdn.net/ajax/libs/echarts/5.5.1/echarts.min.js",
        "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js",
        "https://unpkg.com/echarts@5.5.1/dist/echarts.min.js",
    ]
    for url in candidates:
        try:
            logger.info("下载 echarts.min.js：%s", url)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp, open(local, "wb") as f:
                f.write(resp.read())
            if local.exists() and local.stat().st_size > 100_000:
                logger.info("echarts.min.js 已下载到本地，此后离线可用")
                return "echarts.min.js"
        except Exception as e:
            logger.warning("源 %s 下载失败：%s", url, e)
            continue
    logger.warning("所有 echarts.min.js 下载源均失败，页面将尝试从 CDN 加载")
    return candidates[0]


# ---------------------------------------------------------------------------
# 构建 option 字典
# ---------------------------------------------------------------------------

def _tooltip_js(real_assets: list[float]) -> str:
    """生成 tooltip formatter：按 dataIndex 查真实资产，显示 日期/资产/支出。"""
    assets_json = json.dumps(real_assets)
    return f"""
function(params) {{
    var p = params.find(function(sp) {{ return sp.seriesName === '累计资产'; }});
    if (!p) return '';
    var realAssets = {assets_json};
    var asset = realAssets[p.dataIndex];
    var expense = 0;
    var bar = params.find(function(sp) {{ return sp.seriesName === '每日支出'; }});
    if (bar) expense = bar.value || 0;
    var fmt = function(x) {{
        return Number(x).toLocaleString('zh-CN', {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
    }};
    return '<div style="font-size:14px;font-weight:bold;margin-bottom:4px">' + p.axisValue + '</div>'
        + '<div>资产: <span style="color:{COLOR_ASSET};font-weight:bold">¥' + fmt(asset) + '</span></div>'
        + '<div>支出: <span style="color:#87CEEB">¥' + fmt(expense) + '</span></div>';
}}
"""


def _build_option(
    dates: list[str],
    assets: list[float],
    expenses: list[float],
    fold: dict | None,
) -> dict:
    """构建完整 ECharts option 字典。"""
    # 折叠启用时：资产用显示值绘制，axisLabel 反函数还原真实值
    if fold:
        y_axis_0 = {
            "type": "value",
            "name": "资产（折叠）",
            "position": "left",
            "min": 0,
            "max": 1,
            "splitLine": {"lineStyle": {"type": "dashed", "color": "#eee"}},
            "axisLabel": {"formatter": JsCode(_inverse_js(fold))},
            "nameTextStyle": {"fontSize": 12},
        }
        assets_plot = [round(fold["forward"](v), 6) for v in assets]
    else:
        y_axis_0 = {
            "type": "value",
            "name": "资产",
            "position": "left",
            "splitLine": {"lineStyle": {"type": "dashed", "color": "#eee"}},
            "nameTextStyle": {"fontSize": 12},
        }
        assets_plot = assets

    # 支出条形走独立右轴，锚定横轴、自成一系，避免被资产量级压扁
    y_axis_1 = {
        "type": "value",
        "name": "支出",
        "position": "right",
        "splitLine": {"show": False},
        "axisLabel": {"color": "#aaa", "fontSize": 10},
        "nameTextStyle": {"fontSize": 12},
    }

    return {
        "title": {"text": "资产变化图", "left": "center", "textStyle": {"fontSize": 18}},
        "tooltip": {
            "trigger": "axis",
            "backgroundColor": "rgba(255,255,255,0.96)",
            "borderColor": "#ddd",
            "borderWidth": 1,
            "padding": [10, 14],
            "textStyle": {"color": "#333"},
            "formatter": JsCode(_tooltip_js(assets)),
        },
        "legend": {"data": ["累计资产", "每日支出"], "top": 35},
        "grid": {"left": "3%", "right": "4%", "bottom": "15%", "top": "18%", "containLabel": True},
        "xAxis": {
            "type": "category",
            "data": dates,
            "boundaryGap": True,
            "axisLine": {"onZero": False},
            "axisPointer": {
                "type": "line",
                "lineStyle": {"type": "dashed", "color": "#999", "width": 1},
            },
            "axisLabel": {"rotate": 45, "fontSize": 11},
        },
        "yAxis": [y_axis_0, y_axis_1],
        "series": [
            {
                "name": "每日支出",
                "type": "bar",
                "yAxisIndex": 1,
                "data": [round(e, 2) for e in expenses],
                "barWidth": "60%",
                "itemStyle": {"color": COLOR_EXPENSE},
                "z": 1,
                "emphasis": {"itemStyle": {"color": "rgba(135,206,235,0.8)"}},
            },
            {
                "name": "累计资产",
                "type": "line",
                "yAxisIndex": 0,
                "data": assets_plot,
                "smooth": True,
                "symbol": "circle",
                "symbolSize": 4,
                "lineStyle": {"color": COLOR_ASSET, "width": 2},
                "itemStyle": {"color": COLOR_ASSET},
                "z": 2,
                # 悬停：大圆点
                "emphasis": {"symbolSize": 12, "lineStyle": {"width": 3}},
            },
        ],
        "dataZoom": [
            {"type": "inside", "start": 0, "end": 100},
            {
                "type": "slider",
                "start": 0,
                "end": 100,
                "bottom": 5,
                "height": 20,
                "borderColor": "#ddd",
                "fillerColor": "rgba(173,216,230,0.25)",
            },
        ],
    }


# ---------------------------------------------------------------------------
# 页面模板
# ---------------------------------------------------------------------------

_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>资产变化图</title>
<script src="{echarts_src}"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Microsoft YaHei", sans-serif; background: #f5f6f8; padding: 20px; }}
  .container {{ max-width: 1200px; margin: 0 auto; background: #fff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); padding: 20px; }}
  .controls {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 16px; padding: 10px 14px; background: #f0f1f3; border-radius: 8px; }}
  .controls .sep {{ color: #ccc; margin: 0 4px; user-select: none; }}
  .controls button {{ padding: 5px 14px; border: 1px solid #d0d0d0; border-radius: 6px; background: #fff; cursor: pointer; font-size: 13px; transition: all 0.15s; }}
  .controls button:hover {{ background: #e8f4fd; border-color: #87CEEB; }}
  .controls button.active {{ background: {color_asset}; color: #fff; border-color: {color_asset}; }}
  .controls input[type="date"] {{ padding: 4px 8px; border: 1px solid #d0d0d0; border-radius: 6px; font-size: 13px; }}
  .controls input[type="date"]:focus {{ outline: none; border-color: #87CEEB; }}
</style>
</head>
<body>
<div class="container">
  <div class="controls">
    <button data-range="all" onclick="setRange('all')">全部</button>
    <span class="sep">|</span>
    <button data-range="1m" onclick="setRange('1m')">近1月</button>
    <button data-range="3m" onclick="setRange('3m')">近3月</button>
    <button data-range="6m" onclick="setRange('6m')">近6月</button>
    <button data-range="1y" onclick="setRange('1y')">近1年</button>
    <span class="sep">|</span>
    <span style="font-size:13px;color:#666;">自定义</span>
    <input type="date" id="date-start" title="开始日期">
    <input type="date" id="date-end" title="结束日期">
    <button onclick="applyCustom()">应用</button>
  </div>
  {embed}
</div>
<script>
var PRESETS = {presets};
var MAX_DATE = '{max_date}';

document.getElementById('date-start').value = PRESETS['1m'];
document.getElementById('date-end').value = MAX_DATE;

if (typeof echarts === 'undefined') {{
  var el = document.getElementById('chart');
  if (el) el.innerHTML = '<div style="padding:60px 20px;text-align:center;color:#c0392b;font-size:15px;">⚠ echarts.min.js 加载失败：请检查网络，或将 echarts.min.js 手动放入 output/ 目录后刷新页面</div>';
}} else {{
  var chart = echarts.getInstanceByDom(document.getElementById('chart'));

  function setRange(name) {{
    document.querySelectorAll('.controls button[data-range]').forEach(function(b) {{
      b.classList.toggle('active', b.dataset.range === name);
    }});
    chart.setOption({{ dataZoom: [
      {{ startValue: PRESETS[name], endValue: MAX_DATE }},
      {{ startValue: PRESETS[name], endValue: MAX_DATE }}
    ] }});
  }}

  function applyCustom() {{
    var start = document.getElementById('date-start').value;
    var end = document.getElementById('date-end').value;
    if (!start || !end) return;
    document.querySelectorAll('.controls button[data-range]').forEach(function(b) {{
      b.classList.remove('active');
    }});
    chart.setOption({{ dataZoom: [
      {{ startValue: start, endValue: end }},
      {{ startValue: start, endValue: end }}
    ] }});
  }}
}}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def build_html(daily: pd.DataFrame) -> Path:
    """构建图表 HTML 并写入 output/chart.html，返回文件路径。"""
    if daily.empty:
        logger.warning("无数据可展示")
        return OUTPUT_FILE

    dates = [d.strftime("%Y-%m-%d") for d in daily.index]
    assets = [round(float(v), 2) for v in daily["asset"]]
    expenses = [round(float(v), 2) for v in daily["expense"]]
    max_date = dates[-1]

    # 预设区间：相对最新日期
    max_dt = daily.index.max()
    presets = {"all": dates[0]}
    for key, days in PRESET_DAYS.items():
        presets[key] = (max_dt - timedelta(days=days)).strftime("%Y-%m-%d")

    # 纵轴折叠
    fold = None
    if Y_FOLD_ENABLED:
        try:
            fold = build_y_fold(daily["asset"])
            if fold:
                logger.info("纵轴折叠启用：%d 个折叠段", len(fold["ranges"]))
        except Exception:
            logger.exception("纵轴折叠计算失败，退回正常轴")

    option = _build_option(dates, assets, expenses, fold)

    # 用 PyECharts 渲染可嵌入片段，再套自定义模板
    chart = Bar(init_opts=opts.InitOpts(chart_id="chart", width="100%", height="600px"))
    chart.options = option
    embed = chart.render_embed()
    # 防御：若 render_embed 未输出容器 div，手动补一个
    if "<div" not in embed:
        embed = '<div id="chart" style="width:100%;height:600px"></div>\n' + embed

    html = _TEMPLATE.format(
        echarts_src=_ensure_echarts_js(),
        presets=json.dumps(presets, ensure_ascii=False),
        max_date=max_date,
        embed=embed,
        color_asset=COLOR_ASSET,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)  # _ensure_echarts_js 也会建，保持幂等
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    logger.info("图表已生成：%s", OUTPUT_FILE)
    return OUTPUT_FILE
