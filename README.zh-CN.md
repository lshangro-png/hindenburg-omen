# 兴登堡预兆 (Hindenburg Omen) —— A 股复现

[English](README.md) | **中文**

在 A 股上复现顶部预警指标「兴登堡预兆」，并用**基准率**检验它是否真的有信息量。
参考：浙商证券《"兴登堡预兆"规避股指下跌的有效性如何？——筹码微观结构探秘系列（九）》（2026-06-26）。

- 📄 **研究报告**：[report/report_zh.md](report/report_zh.md)，完整的方法、结果和结论
- 📓 **从这里开始**：[02_回测.ipynb](02_回测.ipynb)，带图从头到尾走一遍研究过程和结论
- 📊 **在线仪表盘**：https://lshangro-png.github.io/hindenburg-omen/
- 🎛 **参数实验室**：https://lshangro-png.github.io/hindenburg-omen/lab.html （拖滑块实时重算）

数据截至 2026-08-07。

## 主要结论

在 A 股上，兴登堡预兆**没有表现出稳定的预警能力**：

- 252 日窗口下 ≤ −10% 回撤的基准率高达 85%~100%，各指数命中率与基准率之差在 −7 到 +9 个百分点之间，有正有负。
- ≤ −20% 口径的超额方向也不一致，而且每个指数可评估的集群只有 4~14 个。
- 2015 年大顶完全没有信号：市场从普涨直接切到普跌，「新高新低同时大量出现」的条件②一天都没成立。

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

整个项目是三个 notebook，它们之间**通过 PostgreSQL 数据库传数据**：

```
01_数据.ipynb   数据：建表 → 一次性导入旧版数据文件 → 用聚宽增量更新
02_回测.ipynb   回测：读库 → 四个条件 → 集群 → 回撤 → 基准率 → 结果写回数据库，
                同时带图表和完整讲解（2015 年案例、五指数汇总、诊断工具）
03_展示.ipynb   展示：从库里读结果 → 生成仪表盘和参数实验室网页到 docs/
assets/         网页需要的前端文件（图表引擎、回测逻辑的 JS 镜像、中英文对照表、样式）
docs/           GitHub Pages：中英文仪表盘和参数实验室
report/         研究报告及配图
```

| 数据库表 | 由谁写入 | 内容 |
|---|---|---|
| `stock_close_adj`、`stock_close_unadj`、`stock_state` | 01 | 个股价格（后复权 / 不复权）和每日交易状态 |
| `index_close`、`index_meta`、`index_constituents` | 01 | 基准指数收盘价和成分股 |
| `breadth` | 02 | 各指数的市场宽度序列（缓存，参数或数据变了会自动重算） |
| `daily` | 02 | 每个指数每天的收盘价、均线、新高新低、McClellan、四个条件、是否信号日 |
| `clusters`、`hit_vs_base`、`summary`、`strategy_info`、`index_pre` | 02 | 集群与回撤、命中率与基准率、汇总统计、参数 |

用 Tableau 做图时直接连这个数据库，用 `daily`、`clusters`、`hit_vs_base` 三张表即可。

## 安装

1. 安装 PostgreSQL 并建库（macOS + Homebrew）：

   ```bash
   brew install postgresql@17
   ```

   ```bash
   brew services start postgresql@17
   ```

   ```bash
   /opt/homebrew/opt/postgresql@17/bin/createdb hindenburg
   ```

2. 安装 Python 依赖：

   ```bash
   pip install -r requirements.txt
   ```

3. 按顺序运行 notebook：`01_数据` → `02_回测` → `03_展示`。

数据库地址默认是 `postgresql+psycopg://localhost/hindenburg`（本机连接不需要密码），要换成别的地址就设置环境变量 `HO_DB_URL`。

聚宽账号只在更新数据时需要，**只通过环境变量提供，不要写进 notebook 或 README**：

```bash
export JQ_USER=你的账号
export JQ_PASSWORD=你的密码
```

第一次运行 `01_数据` 时，会从 `HO_LEGACY_DIR` / `HO_LEGACY_INDEX_DIR` 指定的目录导入旧版数据文件
（`stock_price_adj_close.pkl`、`stock_state.pkl`、`index_constituents_*.pkl`、`index_store/*.parquet`），
格式见 [data/README.md](data/README.md)。

## 使用

- **只更新数据、没改参数**：三个 notebook 从头到尾运行一遍。01 只会拉缺少的部分。
- **改了参数**：改 `02_回测` 开头的 `Params` / `STRATEGIES`，然后运行 02 和 03。宽度序列缓存在数据库里，改了重参数会自动重算。
- **排查某天为什么没信号**：`02_回测` 第 7.1 节，`inspect_day('2015-06-29')`。
- **对比剔不剔 ST**：`02_回测` 第 7.2 节（把 `RUN_COMPARE` 设为 `True`）。

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `HO_DB_URL` | `postgresql+psycopg://localhost/hindenburg` | 数据库地址 |
| `HO_OFFLINE` | 空 | 设为 `1` 跳过所有聚宽更新 |
| `HO_LEGACY_DIR` / `HO_LEGACY_INDEX_DIR` | `data` / `index_store` | 一次性导入用的旧版数据目录 |
| `HO_PAGES_DIR` | `docs` | `03_展示` 输出网页的目录 |
| `JQ_RESERVE` / `JQ_MAX_ROWS` / `JQ_MAX_ROWS_PANEL` | 50000 / 20000 / 500000 | 聚宽额度保护 |
| `JQ_DRY_RUN` | 空 | 设为 `1` 只打印拉取计划 |

## 常见情况

**「⚠ 尾部 N 个交易日可交易域塌陷」**：交易状态表落后于价格了，在 `01_数据` 里两个一起更新。

**各标的起点不一样**：聚宽没有指数发布日之前的数据（中证全指只到 2011-08，
300/500/1000 都从各自发布日开始）。回测会自动从有数据的第一天开始。

**2015 年没信号**：这是指标本身的盲区，不是算错了。条件②要求新高、新低占比同一天都 ≥ 2.2%：
6 月中旬顶部时新高占比 30%~40%，新低几乎为 0；下跌开始后新高很快掉到 1% 以下，
而新低直到 7 月 7~8 日才大量出现，那时条件①也已失效。普涨直接切到普跌，条件②一天都没成立。
逐日数据见 `02_回测` 第 5.5 节。

## 参数口径

全部参数在 `02_回测` 的第一格。几个有多种选法的地方：

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
- 价格增量更新只给库里**已有的**股票续数据，之后新上市的股票不会自动加入。
  它们上市满一年才会进可交易域，所以短期影响有限，但时间长了股票池会逐渐缺新股。
- 本项目仅供研究，不构成投资建议。

## License

[MIT](LICENSE)
