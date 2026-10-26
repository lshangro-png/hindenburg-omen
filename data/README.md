# data/

本目录不入库（见 `.gitignore`）。项目的数据都存在 PostgreSQL 数据库里，这里只放**一次性导入用的旧版数据文件**。
`01_数据.ipynb` 第一次运行时会在这里递归查找下面的文件并导入数据库，之后就不再需要它们。

| 文件 | 导入到 | 格式 |
|---|---|---|
| `stock_price_adj_close.pkl` | `stock_close_adj` | 后复权收盘价宽表，`index` = 交易日，`columns` = 股票代码（`600000.SH` / `000001.SZ` / `830799.BJ`） |
| `stock_state.pkl` | `stock_state` | 每日状态长表，`MultiIndex(trade_dt, stcode)`，列：`trade_status, status_t, maxupdown, isst, issts, issusp, lenlist` |
| `index_constituents_<代码>.pkl` | `index_constituents` | 成分股，`MultiIndex(日期, 代码)`，含 `weight` 列 |
| `unadj_close_*.pkl` | `stock_close_unadj` | 不复权收盘价宽表，格式同上（只给「不复权」对照实验用，可选） |

指数收盘价的旧文件在 `index_store/`（`<代码>.parquet` + `<代码>.meta.json`），导入到 `index_close` 和 `index_meta`。

目录可以用环境变量 `HO_LEGACY_DIR`、`HO_LEGACY_INDEX_DIR` 改到别处。没有旧文件也没关系：配好聚宽账号后，
`01_数据` 会直接从聚宽拉取。
