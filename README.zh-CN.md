# 兴登堡预兆 (Hindenburg Omen) —— A 股复现

[English](README.md) | **中文**

在 A 股上复现顶部预警指标「兴登堡预兆」，并用**基准率**检验它是否真的有信息量。
参考：浙商证券《"兴登堡预兆"规避股指下跌的有效性如何？——筹码微观结构探秘系列（九）》（2026-06-26）。

- 📓 **从这里开始**：[walkthrough_zh.ipynb](walkthrough_zh.ipynb)，带图从头到尾走一遍研究过程和结论（[English](walkthrough.ipynb)）
- 📊 **在线仪表盘**：https://lshangro-png.github.io/hindenburg-omen/
- 🎛 **参数实验室**：https://lshangro-png.github.io/hindenburg-omen/lab.html （拖滑块实时重算）

数据截至 2026-08-07。

## 主要结论

在 A 股上，兴登堡预兆**没有表现出稳定的预警能力**：

- 252 日窗口下 ≤ −10% 回撤的基准率高达 85%~100%，各指数命中率与基准率之差在 −7 到 +9 个百分点之间，有正有负。
- ≤ −20% 口径的超额方向也不一致，而且每个指数可评估的集群只有 4~14 个。
- 2015 年大顶完全没有信号：市场从普涨直接切到普跌，「新高新低同时大量出现」的条件②一天都没成立。

详细推导见 [walkthrough_zh.ipynb](walkthrough_zh.ipynb)。

## 指标定义

四个条件同一天全部满足才算触发一次信号：

| | 条件 |
|---|---|
| ① | 50 日均线在上升（`MA(t) > MA(t-5)`） |
| ② | 创 52 周新高、新低的股票占比**都** ≥ 2.2% |
| ③ | 创新高股票数 ≤ 创新低股票数 × 2 |
| ④ | McClellan 振荡器（涨跌家数差的 EMA19 − EMA39）为负 |

单点信号在 36 个交易日内重复出现 ≥2 次，升级为「有效集群」。然后统计每个集群往后
5/21/63/252 个交易日的最大回撤，按 252 日回撤定级（≤-20% 较大回撤 / ≤-10% 中型回调 /
≤-5% 小幅回调），算出命中率。

**同时会算基准率**，也就是不看任何信号、随便挑一天，未来一年出现同等回撤的概率。
A 股 252 日窗口下这个数高达 85%~100%，所以除了「命中率 92%」这种数字，还会算
**超额 = 命中率 − 基准率**。

回测五个标的：中证全指、上证综指、沪深300、中证500、中证1000。每个标的的条件①和回撤
都用它自己的指数，条件②③④用对应的股票池（全市场 / 仅沪市 / 各自成分股）。

## 项目结构

```
.
├── walkthrough_zh.ipynb  研究走读（中文）：调用下面的模块，从数据到结论完整演示
├── walkthrough.ipynb  同一份研究走读的英文版
├── hindenburg.py      命令行入口（update / run / viz / inspect-day ...）
├── backtest.py        回测核心纯函数：build_signals → build_clusters → evaluate → summarize
├── config.py          全部参数与策略定义（轻参数 / 重参数分层）
├── data.py            数据层：价格面板、可交易域、新高新低、McClellan、各级本地缓存
├── jq_source.py       聚宽数据源（唯一的远端），含额度保护与面板增量更新
├── viz.py             生成回测仪表盘（中文 dashboard.html / 英文 dashboard_en.html）
├── lab.py             参数实验室（lab.html / lab_en.html），浏览器里拖滑块实时重算轻参数
├── export_unadj.py    实验性对照口径：不复权价 + 不过滤可交易域（仅进 lab）
├── assets/
│   ├── backtest.js    回测核心的 JS 镜像，与 backtest.py 逐函数对应
│   ├── common.js      仪表盘和参数实验室共用的前端小工具
│   ├── i18n.js        页面上所有文字的中英对照表
│   ├── chart_engine.js  轻量 SVG 图表引擎
│   └── report.css
├── docs/              GitHub Pages：仪表盘和参数实验室的快照
├── data/              本地数据（不入库，格式见 data/README.md）
└── output/            运行产出：clusters_*.csv / export_*.json / *.html（不入库）
```

运行时还会生成 `.cache/`（宽度指标缓存）和 `index_store/`（指数收盘 parquet），都不入库。

## 安装

```bash
pip install -r requirements.txt
```

数据准备见 [data/README.md](data/README.md)。

聚宽账号（可选，不配也能跑纯本地模式），**只通过环境变量提供，不要写进代码或 README**：

```bash
export JQ_USER=你的账号
export JQ_PASSWORD=你的密码
```

## 使用

**日常更新（只更新数据，没改参数）：**

```bash
python hindenburg.py update-panel    # 价格面板 + 状态表更新到最新交易日
python hindenburg.py run             # 跑回测，打印结果，写 output/clusters_*.csv
python hindenburg.py viz             # 生成 output/dashboard.html 和 dashboard_en.html
```

不需要 `clear-cache`，因为缓存键里带了数据文件的修改时间，数据一更新缓存就自动失效重算。

**改过 `config.py` 参数之后：**

```bash
python hindenburg.py clear-cache
python hindenburg.py run
python hindenburg.py viz
```

**只跑一个标的：**

```bash
python hindenburg.py run --strategy ashare
# 可选：ashare(中证全指) / general(上证综指) / 300 / 500 / 1000
```

**只重新生成页面、不重跑回测：**

```bash
python hindenburg.py viz --reuse     # 直接用上次导出的 JSON
```

**参数实验室**（拖滑块实时看结果，不用重跑）：

```bash
python hindenburg.py export
python lab.py                        # 生成 output/lab.html 和 lab_en.html
```

**「不复权价 + 不过滤」实验性对照口径**（只在参数实验室里能看到，不进 dashboard；
这不是本项目认可的正式口径，只作对照）：

```bash
python export_unadj.py data
python lab.py
```

**排查某天为什么没信号：**

```bash
python hindenburg.py inspect-day 2015-06-29 --strategy ashare
```

会把那几天四个条件的原始值全部打印出来，能直接看出卡在哪个条件。

**其他命令：**

```bash
python hindenburg.py quota           # 查聚宽剩余额度
python hindenburg.py stored          # 查本地缓存了哪些指数
python hindenburg.py inspect         # 查状态表字段分布
python hindenburg.py compare         # 对比剔 ST / 不剔 ST 的影响
python hindenburg.py update --reset-head 000985.CSI   # 强制重试拉取指数历史
```

**环境变量：**

| 变量 | 默认 | 说明 |
|---|---|---|
| `HO_DATA_DIR` | `data` | 本地面板数据目录 |
| `HO_OUT_DIR` | `output` | 结果输出目录 |
| `HO_CACHE_DIR` / `HO_INDEX_DIR` | `.cache` / `index_store` | 缓存目录 |
| `HO_OFFLINE` | 空 | 设为 `1` 强制纯本地，不联网 |
| `HO_START` / `HO_END` | `2000-01-01` / 面板最后一天 | 默认回测区间 |
| `JQ_RESERVE` / `JQ_MAX_ROWS` / `JQ_MAX_ROWS_PANEL` | 50000 / 20000 / 500000 | 聚宽额度保护 |
| `JQ_DRY_RUN` | 空 | 设为 `1` 只打印拉取计划 |

## 常见情况

**「⚠ 尾部 N 个交易日可交易域塌陷」**：`stock_state.pkl` 落后于价格面板了，
跑 `python hindenburg.py update-panel`，两个文件会一起更新。

**各标的起点不一样**：聚宽没有指数发布日之前的数据（中证全指只到 2011-08，
300/500/1000 都从各自发布日开始）。回测会自动从有数据的第一天开始。

**2015 年没信号**：这是指标本身的盲区，不是算错了。条件②要求新高、新低占比同一天都 ≥ 2.2%：
6 月中旬顶部时新高占比 30%~40%，新低几乎为 0；下跌开始后新高很快掉到 1% 以下，
而新低直到 7 月 7~8 日才大量出现，那时条件①也已失效。普涨直接切到普跌，条件②一天都没成立。
逐日数据见 walkthrough_zh.ipynb 第 6 节。

## 参数口径

全部参数在 `config.py`。几个有多种选法的地方：

| 参数 | 当前值 | 说明 |
|---|---|---|
| `trend_mode` | `ma_rising` | 条件①按**均线斜率**判定（`MA(t) > MA(t-5)`），美股原始定义。另一个选项 `above_ma`（价格站上均线，浙商适配版口径）实测对 2015 那类闪崩同样无效 |
| `cluster_mode` | `fixed` | 集群窗口**从首日固定** 36 个交易日。`chain` 是链式延展，会把跨度几个月的信号串成一个巨型集群 |
| `dd_mode` | `peak_trough` | **峰谷最大回撤**，"最大回撤"的标准定义。`from_signal` 固定拿起点价格当分母 |
| `dd_anchor` | `confirm` | 回撤从**集群确认日**（第 2 个信号出现那天）起算。研报从首日起算，但首日当天还不知道它会不会升级成有效集群，有前视偏差 |
| `mc_norm` | `raw` | McClellan 用原始涨跌家数差。`rana` 按家数归一化 |
| `state_rule` | `rule_default` | 可交易域**不额外剔 ST**。五个标的统一，口径一致才可比 |

其余数值参数：

```
条件①   ma_period=50, ma_lookback=5
条件②   threshold=0.022 (2.2%)
条件③   hl_ratio=2.0
条件④   ema_short=19, ema_long=39
集群     cluster_window=36, cluster_min=2
回撤     dd_windows=(5,21,63,252), grade_window=252, grade_cuts=(-20%, -10%, -5%)
新高低   hl_window=252, hl_min_periods=200
其他     warmup=260, exclude_boards=('BJ',), min_coverage=0.5
```

`include_exchanges` 只有上证综指设了 `('SH',)`（它只含沪市股票），其余标的为空。

## 已知局限

- 每个指数可评估的有效集群只有 4~14 个，命中率的置信区间很宽。
- `update-panel` 只给面板里**已有的**股票续数据，面板建好之后新上市的股票不会自动加列。
  它们上市满一年才会进可交易域，所以短期影响有限，但长期不重建面板会让股票池逐渐缺新股。
- 本项目仅供研究，不构成投资建议。

## License

[MIT](LICENSE)
