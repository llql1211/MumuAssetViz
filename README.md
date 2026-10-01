# MumuAssetViz | 资产变化可视化

基于 xlsx 账单文件，生成资产累计折线 + 每月结余交互图的本地工具。

## 功能

- **每日资产变化图**：累计资产折线 + 每日支出浅色条形，悬停显示详细信息
- **每月结余图**：每月净收支条形（正绿负红、零点居中）+ 累计资产折线
- **平均值参考线**：两张图各有一条虚线，显示当前缩放窗口内每日支出 / 每月结余的平均值，切换时间段或拖拽缩放时实时更新
- **时间段切换**：全部 / 近1月 / 3月 / 6月 / 1年 / 3年 / 自定义日期 / 按年筛选 / 按月筛选（月图为近1年 / 3年 / 5年 / 自定义月份 / 按年筛选）
- **离线可用**：echarts.min.js 打包在项目内，生成后无需网络

## 快速开始

1. 将 xlsx 账单文件放入 [data/input/](data/input/) 目录（只保留一个）
2. 安装依赖（二选一）：

   ```bash
   # pip
   pip install pandas pyecharts openpyxl

   # pixi
   pixi install
   ```

3. 运行：

   ```bash
   pixi run python main.py
   ```

4. 浏览器自动打开 `data/output.html`

## 目录

```text
MumuAssetViz/
├── main.py              # 入口
├── app/                 # 全部代码
│   ├── paths.py         # 路径常量的唯一来源
│   ├── parse.py         # xlsx 解析与数据清洗
│   ├── aggregate.py     # 按天/按月聚合
│   ├── cache.py         # 缓存与失效校验
│   └── chart.py         # ECharts 图表构建与页面模板
├── assets/
│   └── echarts.min.js   # ECharts 前端库（随仓库提交，离线）
├── data/                # 全部数据
│   ├── input/           # 放 xlsx 账单
│   ├── archived/        # 手工归档的旧账单
│   ├── cache.pkl         # 解析结果缓存，可随时删
│   └── output.html       # 生成的图表页面
└── dev_notes/
```

代码只从 `app/paths.py` 取路径，不各写各的；`data/` 是运行时产物（不入库），`assets/` 是随仓库提交的静态资源。

## xlsx 格式要求

必需列：`时间`、`类型`、`金额`。`类型` 为「收入」或「支出」，`金额` 支出为负。

可选列（用于悬停时展示当日支出明细）：`分类`（或 `一级分类`）、`二级分类`、`备注`。缺少或留空时，悬停提示中对应位置显示为空。

## 缓存

首次解析后聚合结果写入 `data/cache.pkl`，xlsx 未变化时后续运行直接读取，跳过解析。文件可随时删除，下次运行自动重建。
