# data/

本目录不入库（见 `.gitignore`）。运行前把以下文件放进来，子目录也可以，程序会递归查找：

| 文件 | 必需 | 格式 |
|---|---|---|
| `stock_price_adj_close.pkl` | ✅ | 后复权收盘价宽表，`index` = 交易日，`columns` = 股票代码（`600000.SH` / `000001.SZ` / `830799.BJ`） |
| `stock_state.pkl` | 可选 | 每日状态长表，`MultiIndex(trade_dt, stcode)`，列：`trade_status, status_t, maxupdown, isst, issts, issusp, lenlist`。缺失时退化为按价格推断可交易域 |
| `index_constituents_<代码>.pkl` | 可选 | 成分股，`MultiIndex(date, code)`，含 `weight` 列。缺失且配了聚宽账号时自动拉取 |

数据目录可用环境变量 `HO_DATA_DIR` 改到别处。
