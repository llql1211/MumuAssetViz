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
from pyecharts.commons.utils import JsCode  # noqa: F401  # 保留 pyecharts 依赖引用
from pyecharts.globals import CurrentConfig

# ---- 自控序列化：不依赖 pyecharts JsCode 的内部格式 ----

class _RawJS:
    """标记一段原始 JS 代码，_dump_option 会内联为不带引号的原始文本。"""
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_FILE = OUTPUT_DIR / "chart.html"

# 优先使用项目内打包的 echarts.min.js（随仓库提交，运行时零下载）
ASSETS_DIR = Path(__file__).parent / "assets"
BUNDLED_ECHARTS = ASSETS_DIR / "echarts.min.js"
# 页面在 output/chart.html，指向 assets/echarts.min.js 的相对路径
BUNDLED_ECHARTS_SRC = "../assets/echarts.min.js"

# ---- 时间段预设 ----
# 主图（每日资产）：相对最新日期的天数
PRESET_DAYS = {"1m": 30, "3m": 90, "6m": 180, "1y": 365, "3y": 1095}
# 月图（每月结余）：相对最新月份的年数
MONTH_PRESET_YEARS = {"1y": 1, "3y": 3, "5y": 5}

# ---- 颜色 ----
COLOR_ASSET = "#FF6B6B"          # 资产折线：珊瑚红
COLOR_EXPENSE = "rgba(173,216,230,0.55)"  # 支出条形：浅蓝
COLOR_POS = "#2ecc71"            # 每月结余为正：绿
COLOR_NEG = "#e74c3c"            # 每月结余为负：红
COLOR_AVG = "#888888"            # 平均值虚线


def _fmt_money(x: float) -> str:
    """金额 → "¥1,234.56"（千分位 + 两位小数）。"""
    return f"¥{x:,.2f}"


# ---------------------------------------------------------------------------
# option 序列化
# ---------------------------------------------------------------------------

def _dump_option(option: dict) -> str:
    """将 option 序列化为 JS 对象字面量；JsCode 内联为原始 JS（不带引号）。

    不再依赖 pyecharts 的 render_embed()（它返回完整 HTML 页面，无法内嵌），
    改为自控序列化后，直接在模板里 echarts.init().setOption()。
    """
    codes: list[str] = []

    def walk(o):
        if isinstance(o, _RawJS):
            idx = len(codes)
            codes.append(o.code)
            return f"__RAWJS_{idx}__"
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [walk(v) for v in o]
        return o

    cleaned = walk(option)
    s = json.dumps(cleaned, ensure_ascii=False)
    for i, code in enumerate(codes):
        s = s.replace(f'"__RAWJS_{i}__"', code)
    return s


# ---------------------------------------------------------------------------
# echarts.min.js 本地化
# ---------------------------------------------------------------------------

def _ensure_echarts_js() -> str:
    """返回页面引用的 echarts.min.js 路径。

    优先级：项目内打包的 assets/echarts.min.js → output/ 下已下载的副本
    → 运行时下载。仅当前两者都不存在才下载，且成功后落盘，下次直接复用。
    """
    if BUNDLED_ECHARTS.exists() and BUNDLED_ECHARTS.stat().st_size > 100_000:
        return BUNDLED_ECHARTS_SRC

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
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

def _tooltip_js(real_assets: list[float], daily_top: dict) -> str:
    """生成 tooltip formatter：日期/资产/支出 + 当日支出 Top N 明细。"""
    assets_json = json.dumps(real_assets)
    top_json = json.dumps(daily_top, ensure_ascii=False)
    return f"""
function(params) {{
    var p = params.find(function(sp) {{ return sp.seriesName === '累计资产'; }});
    if (!p) return '';
    var realAssets = {assets_json};
    var asset = realAssets[p.dataIndex];
    var expense = 0;
    var bar = params.find(function(sp) {{ return sp.seriesName === '每日支出'; }});
    if (bar) expense = bar.value || 0;
    var fmt2 = function(x) {{
        return Number(x).toLocaleString('zh-CN', {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
    }};
    var fmt0 = function(x) {{
        return Number(x).toLocaleString('zh-CN', {{minimumFractionDigits: 0, maximumFractionDigits: 2}});
    }};
    var html = '<div style="font-size:14px;font-weight:bold;margin-bottom:4px">' + p.axisValue + '</div>'
        + '<div>资产: <span style="color:{COLOR_ASSET};font-weight:bold">¥' + fmt2(asset) + '</span></div>'
        + '<div>支出: <span style="color:#87CEEB;font-weight:bold">¥' + fmt2(expense) + '</span></div>';
    var top = {top_json};
    var items = top[p.axisValue] || [];
    if (items.length > 0) {{
        html += '<div style="margin:6px 0 2px;padding-top:5px;border-top:1px solid #e8e8e8;font-size:12px;color:#999;">--- Top ' + items.length + ' ---</div>';
        for (var i = 0; i < items.length; i++) {{
            var it = items[i];
            var parts = [it.c1, it.c2, it.n];
            var cat = '';
            if (parts.some(function(x) {{ return x; }})) cat = parts.join('-');
            html += '<div style="font-size:12px;color:#555;padding-top:2px;white-space:nowrap;">'
                + (i + 1) + '. <span style="font-weight:bold">¥' + fmt0(it.a) + '</span> ' + cat + '</div>';
        }}
    }} else if (expense <= 0) {{
        html += '<div style="margin-top:6px;padding-top:4px;border-top:1px solid #e8e8e8;font-size:12px;color:#aaa;">当日无支出</div>';
    }}
    return html;
}}
"""


def _build_option(
    dates: list[str],
    assets: list[float],
    expenses: list[float],
    daily_top: dict,
) -> dict:
    """构建完整 ECharts option 字典。"""
    avg_expense = sum(expenses) / len(expenses) if expenses else 0.0
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
        "tooltip": {
            "trigger": "axis",
            "backgroundColor": "rgba(255,255,255,0.96)",
            "borderColor": "#ddd",
            "borderWidth": 1,
            "padding": [10, 14],
            "textStyle": {"color": "#333"},
            "formatter": _RawJS(_tooltip_js(assets, daily_top)),
        },
        "legend": {"data": ["累计资产", "每日支出"], "top": 35, "textStyle": {"color": "#222"}},
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
                "id": "daily-expense",
                "name": "每日支出",
                "type": "bar",
                "yAxisIndex": 1,
                "data": [round(e, 2) for e in expenses],
                "barWidth": "60%",
                "itemStyle": {"color": COLOR_EXPENSE},
                "z": 1,
                "emphasis": {"itemStyle": {"color": "rgba(135,206,235,0.8)"}},
                "markLine": {
                    "silent": True,
                    "symbol": "none",
                    "lineStyle": {"type": "dashed", "color": COLOR_AVG, "width": 1},
                    "label": {
                        "formatter": f"平均值：{_fmt_money(avg_expense)}",
                        "position": "insideEndTop",
                        "fontSize": 12,
                        "color": "#666",
                    },
                    "data": [{"yAxis": round(avg_expense, 2)}],
                },
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


def _build_monthly_option(
    months: list[str],
    nets: list[float],
    assets: list[float],
) -> dict:
    """构建每月结余图 option。

    - 每月净结余：绿/红条形（正绿负红），右轴对称取值 → 零点居中；
    - 每月末累计资产：折线，走独立左轴，避免被结余量级压扁。
    """
    max_abs = max(abs(v) for v in nets) if nets else 0.0
    m = max_abs * 1.1 if max_abs > 0 else 1.0  # 右轴 min/max 对称，零点居中
    avg_net = sum(nets) / len(nets) if nets else 0.0

    bar_color = _RawJS(
        f"function(params) {{ return params.value >= 0 ? '{COLOR_POS}' : '{COLOR_NEG}'; }}"
    )

    tooltip_js = f"""
function(params) {{
    var bar = params.find(function(sp) {{ return sp.seriesName === '每月结余'; }});
    if (!bar) return '';
    var line = params.find(function(sp) {{ return sp.seriesName === '累计资产'; }});
    var net = bar.value;
    var asset = line ? line.value : '';
    var fmt = function(x) {{
        return Number(x).toLocaleString('zh-CN', {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
    }};
    var color = net >= 0 ? '{COLOR_POS}' : '{COLOR_NEG}';
    return '<div style="font-size:14px;font-weight:bold;margin-bottom:4px">' + bar.axisValue + '</div>'
        + '<div>每月结余: <span style="color:' + color + ';font-weight:bold">¥' + fmt(net) + '</span></div>'
        + '<div>累计资产: <span style="color:{COLOR_ASSET};font-weight:bold">¥' + fmt(asset) + '</span></div>';
}}
"""

    return {
        "tooltip": {
            "trigger": "axis",
            "backgroundColor": "rgba(255,255,255,0.96)",
            "borderColor": "#ddd",
            "borderWidth": 1,
            "padding": [10, 14],
            "textStyle": {"color": "#333"},
            "formatter": _RawJS(tooltip_js),
        },
        "legend": {"data": ["每月结余", "累计资产"], "top": 35, "textStyle": {"color": "#222"}},
        "grid": {"left": "3%", "right": "4%", "bottom": "15%", "top": "18%", "containLabel": True},
        "xAxis": {
            "type": "category",
            "data": months,
            "axisPointer": {
                "type": "line",
                "lineStyle": {"type": "dashed", "color": "#999", "width": 1},
            },
            "axisLabel": {"rotate": 45, "fontSize": 11},
        },
        "yAxis": [
            {
                "type": "value",
                "name": "累计资产",
                "position": "left",
                "splitLine": {"lineStyle": {"type": "dashed", "color": "#eee"}},
                "nameTextStyle": {"fontSize": 12},
            },
            {
                "type": "value",
                "name": "每月结余",
                "position": "right",
                "min": -m,
                "max": m,
                "splitLine": {"show": False},
                "axisLabel": {"fontSize": 10},
                "nameTextStyle": {"fontSize": 12},
            },
        ],
        "series": [
            {
                "id": "month-net",
                "name": "每月结余",
                "type": "bar",
                "yAxisIndex": 1,
                "data": [round(v, 2) for v in nets],
                "barWidth": "50%",
                "itemStyle": {"color": bar_color},
                "markLine": {
                    "silent": True,
                    "symbol": "none",
                    "lineStyle": {"color": "#bbb", "type": "dashed"},
                    "data": [
                        {"yAxis": 0},
                        {
                            "yAxis": round(avg_net, 2),
                            "lineStyle": {"type": "dashed", "color": COLOR_AVG},
                            "label": {
                                "formatter": f"平均值：{_fmt_money(avg_net)}",
                                "position": "insideEndTop",
                                "fontSize": 12,
                                "color": "#666",
                            },
                        },
                    ],
                },
            },
            {
                "name": "累计资产",
                "type": "line",
                "yAxisIndex": 0,
                "data": [round(v, 2) for v in assets],
                "smooth": True,
                "symbol": "circle",
                "symbolSize": 4,
                "lineStyle": {"color": COLOR_ASSET, "width": 2},
                "itemStyle": {"color": COLOR_ASSET},
                "z": 2,
                "emphasis": {"symbolSize": 10},
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
  .controls input[type="date"], .controls input[type="month"] {{ padding: 4px 8px; border: 1px solid #d0d0d0; border-radius: 6px; font-size: 13px; }}
  .controls input[type="date"]:focus, .controls input[type="month"]:focus {{ outline: none; border-color: #87CEEB; }}
  #chart, #chart_month {{ width: 100%; height: 600px; }}
</style>
</head>
<body>
<div class="container">
  <h2 style="font-size:17px;text-align:center;color:#333;margin-bottom:6px;">每日变化</h2>
  <p style="font-size:12px;text-align:center;color:#999;margin:0 0 10px;">可以点击图例按钮隐藏曲线</p>
  <div class="controls" data-chart="daily">
    <button data-range="all" class="active" onclick="setRange('all')">全部</button>
    <span class="sep">|</span>
    <button data-range="1m" onclick="setRange('1m')">近1月</button>
    <button data-range="3m" onclick="setRange('3m')">近3月</button>
    <button data-range="6m" onclick="setRange('6m')">近6月</button>
    <button data-range="1y" onclick="setRange('1y')">近1年</button>
    <button data-range="3y" onclick="setRange('3y')">近3年</button>
    <span class="sep">|</span>
    <span style="font-size:13px;color:#666;">自定义</span>
    <input type="date" id="date-start" title="开始日期">
    <input type="date" id="date-end" title="结束日期">
    <button onclick="applyCustomDaily()">应用</button>
  </div>
  <div id="chart"></div>

  <hr style="margin:28px 0;border:none;border-top:1px solid #eee;">
  <h2 style="font-size:17px;text-align:center;color:#333;margin-bottom:6px;">每月结余</h2>
  <p style="font-size:12px;text-align:center;color:#999;margin:0 0 10px;">可以点击图例按钮隐藏曲线</p>
  <div class="controls" data-chart="month">
    <button data-range="all" onclick="setRangeMonth('all')">全部</button>
    <span class="sep">|</span>
    <button data-range="1y" class="active" onclick="setRangeMonth('1y')">近1年</button>
    <button data-range="3y" onclick="setRangeMonth('3y')">近3年</button>
    <button data-range="5y" onclick="setRangeMonth('5y')">近5年</button>
    <span class="sep">|</span>
    <span style="font-size:13px;color:#666;">自定义</span>
    <input type="month" id="month-start" title="开始月份">
    <input type="month" id="month-end" title="结束月份">
    <button onclick="applyCustomMonth()">应用</button>
  </div>
  <div id="chart_month"></div>
</div>
<script>
var PRESETS = {presets};
var MAX_DATE = '{max_date}';
var MONTH_PRESETS = {month_presets};
var LAST_MONTH = '{last_month}';
var OPTION_DAILY = {option_daily};
var OPTION_MONTH = {option_month};

if (typeof echarts === 'undefined') {{
  var el = document.getElementById('chart');
  if (el) el.innerHTML = '<div style="padding:60px 20px;text-align:center;color:#c0392b;font-size:15px;">⚠ echarts.min.js 加载失败：请检查网络，或将 echarts.min.js 手动放入 output/ 目录后刷新页面</div>';
}} else {{
  var chartDaily = echarts.init(document.getElementById('chart'));
  var chartMonth = echarts.init(document.getElementById('chart_month'));
  chartDaily.setOption(OPTION_DAILY);
  chartMonth.setOption(OPTION_MONTH);

  document.getElementById('date-start').value = PRESETS['1m'];
  document.getElementById('date-end').value = MAX_DATE;
  document.getElementById('month-start').value = MONTH_PRESETS['1y'];
  document.getElementById('month-end').value = LAST_MONTH;

  // ---- 平均值虚线：随缩放窗口动态重算 ----
  var DAILY_DATES = OPTION_DAILY.xAxis.data;
  var MONTH_DATES = OPTION_MONTH.xAxis.data;
  var DAILY_EXPENSES = OPTION_DAILY.series[0].data;
  var MONTH_NETS = OPTION_MONTH.series[0].data;

  function fmtAvg(x) {{
    return '平均值：¥' + Number(x).toLocaleString('zh-CN', {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
  }}

  function visibleRange(chart, labels) {{
    // 从当前 dataZoom 状态取可见窗口 [s, e]。
    // 类目轴上 ECharts 会把 startValue/endValue 归一化成类目索引，
    // 直接 Number() 即可；缺失/非法时回退到百分比换算。
    var dz = chart.getOption().dataZoom[0];
    var n = labels.length;
    var s = -1, e = -1;
    if (dz && dz.startValue != null && dz.endValue != null) {{
      s = Number(dz.startValue);
      e = Number(dz.endValue);
    }}
    if (isNaN(s) || isNaN(e) || s < 0 || e < 0) {{
      s = Math.round(dz.start / 100 * (n - 1));
      e = Math.round(dz.end / 100 * (n - 1));
    }}
    s = Math.max(0, Math.min(n - 1, s));
    e = Math.max(0, Math.min(n - 1, e));
    return [s, e];
  }}

  function avgOf(data, s, e) {{
    var sum = 0, cnt = 0;
    for (var i = s; i <= e; i++) {{ sum += data[i] || 0; cnt++; }}
    return cnt ? sum / cnt : 0;
  }}

  function updateDailyAvg() {{
    var r = visibleRange(chartDaily, DAILY_DATES);
    var avg = avgOf(DAILY_EXPENSES, r[0], r[1]);
    chartDaily.setOption({{ series: [{{ id: 'daily-expense', markLine: {{
      data: [{{ yAxis: avg, label: {{ formatter: fmtAvg(avg) }} }}]
    }} }}] }});
  }}

  function updateMonthAvg() {{
    var r = visibleRange(chartMonth, MONTH_DATES);
    var avg = avgOf(MONTH_NETS, r[0], r[1]);
    chartMonth.setOption({{ series: [{{ id: 'month-net', markLine: {{
      data: [
        {{ yAxis: 0 }},
        {{ yAxis: avg, label: {{ formatter: fmtAvg(avg) }} }}
      ]
    }} }}] }});
  }}

  function zoomTo(chart, start, end) {{
    chart.setOption({{ dataZoom: [
      {{ startValue: start, endValue: end }},
      {{ startValue: start, endValue: end }}
    ] }});
    updateDailyAvg();
    updateMonthAvg();
  }}

  chartDaily.on('datazoom', updateDailyAvg);
  chartMonth.on('datazoom', updateMonthAvg);

  // 主图（每日资产）时间段
  function setRange(name) {{
    document.querySelectorAll('.controls[data-chart="daily"] button[data-range]').forEach(function(b) {{
      b.classList.toggle('active', b.dataset.range === name);
    }});
    zoomTo(chartDaily, PRESETS[name], MAX_DATE);
  }}

  function applyCustomDaily() {{
    var s = document.getElementById('date-start').value;
    var e = document.getElementById('date-end').value;
    if (!s || !e) return;
    document.querySelectorAll('.controls[data-chart="daily"] button[data-range]').forEach(function(b) {{
      b.classList.remove('active');
    }});
    zoomTo(chartDaily, s, e);
  }}

  // 月图（每月结余）时间段
  function setRangeMonth(name) {{
    document.querySelectorAll('.controls[data-chart="month"] button[data-range]').forEach(function(b) {{
      b.classList.toggle('active', b.dataset.range === name);
    }});
    zoomTo(chartMonth, MONTH_PRESETS[name], LAST_MONTH);
  }}

  function applyCustomMonth() {{
    var s = document.getElementById('month-start').value;
    var e = document.getElementById('month-end').value;
    if (!s || !e) return;
    document.querySelectorAll('.controls[data-chart="month"] button[data-range]').forEach(function(b) {{
      b.classList.remove('active');
    }});
    zoomTo(chartMonth, s, e);
  }}

  // 初始视图：主图全部、月图近1年
  setRange('all');
  setRangeMonth('1y');
}}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def build_html(daily: pd.DataFrame, daily_top: dict | None = None) -> Path:
    """构建图表 HTML 并写入 output/chart.html，返回文件路径。"""
    if daily.empty:
        logger.warning("无数据可展示")
        return OUTPUT_FILE
    daily_top = daily_top or {}

    dates = [d.strftime("%Y-%m-%d") for d in daily.index]
    assets = [round(float(v), 2) for v in daily["asset"]]
    expenses = [round(float(v), 2) for v in daily["expense"]]
    max_date = dates[-1]

    # 预设区间：相对最新日期
    max_dt = daily.index.max()
    presets = {"all": dates[0]}
    for key, days in PRESET_DAYS.items():
        presets[key] = (max_dt - timedelta(days=days)).strftime("%Y-%m-%d")

    option = _build_option(dates, assets, expenses, daily_top)

    # 每月数据：每月末累计资产 + 每月净结余（可由日数据直接推出）
    monthly_asset = daily["asset"].resample("ME").last()
    monthly_net = monthly_asset.diff().fillna(monthly_asset.iloc[0])
    months = [ts.strftime("%Y-%m") for ts in monthly_asset.index]
    nets = [round(float(v), 2) for v in monthly_net]
    assets_m = [round(float(v), 2) for v in monthly_asset]
    option_month = _build_monthly_option(months, nets, assets_m)

    # 月图时间段预设：相对最新月份往前推 N 年
    last_month = months[-1] if months else ""
    month_presets = {"all": months[0]} if months else {"all": ""}
    if len(monthly_asset) > 0:
        last_ts = monthly_asset.index[-1]
        for key, years in MONTH_PRESET_YEARS.items():
            month_presets[key] = (last_ts - pd.DateOffset(years=years)).strftime("%Y-%m")

    # 序列化 option 为 JS 字面量（JsCode 内联），模板里直接 init+setOption
    option_daily = _dump_option(option)
    option_month = _dump_option(option_month)

    html = _TEMPLATE.format(
        echarts_src=_ensure_echarts_js(),
        presets=json.dumps(presets, ensure_ascii=False),
        max_date=max_date,
        month_presets=json.dumps(month_presets, ensure_ascii=False),
        last_month=last_month,
        option_daily=option_daily,
        option_month=option_month,
        color_asset=COLOR_ASSET,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)  # _ensure_echarts_js 也会建，保持幂等
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    logger.info("图表已生成：%s", OUTPUT_FILE)
    return OUTPUT_FILE
