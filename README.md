# MumuAssetViz | 资产变化可视化

基于 xlsx 账单文件，生成资产累计折线 + 每月结余交互图的本地工具。

## 功能

- **每日资产变化图**：累计资产折线 + 每日支出浅色条形，悬停显示详细信息
- **每月结余图**：每月净收支条形（正绿负红、零点居中）+ 累计资产折线
- **时间段切换**：近1月 / 3月 / 6月 / 1年 / 3年 / 全部 / 自定义
- **纵轴折叠**：异常跳跃自动压缩，折线细节更清晰
- **离线可用**：echarts.min.js 打包在项目内，生成后无需网络

## 快速开始

1. 将 xlsx 账单文件放入 `input/` 目录（只保留一个）
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

4. 浏览器自动打开 `output/chart.html`

## 目录

```text
MumuAssetViz/
├── main.py              # 入口
├── parse.py             # xlsx 解析与数据清洗
├── aggregate.py         # 按天/按月聚合
├── cache.py             # 缓存与失效校验
├── chart.py             # ECharts 图表构建与页面模板
├── assets/
│   └── echarts.min.js   # ECharts 前端库（离线）
├── input/               # 放 xlsx 账单
└── output/              # 生成的 chart.html
```

## xlsx 格式要求

表头需包含 `时间`、`类型`、`金额` 三列。`类型` 为「收入」或「支出」，`金额` 支出为负。其余列忽略。

## 缓存

首次解析后聚合结果写入 `cache/daily.pkl`，xlsx 未变化时后续运行直接读取，跳过解析。
