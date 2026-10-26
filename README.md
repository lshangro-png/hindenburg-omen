# Hindenburg Omen — A Reproduction on China A-Shares

**English** | [中文](README.zh-CN.md)

A reproduction of the Hindenburg Omen, a market-top warning indicator, on China's A-share market,
tested against **base rates** to check whether it carries any real information.
Reference: Zheshang Securities, *How effective is the "Hindenburg Omen" at avoiding index declines?
— Chip Microstructure Series (IX)* (2026-06-26, in Chinese).

- 📄 **Research report** (Chinese): [report/report_zh.md](report/report_zh.md) — full method, results and conclusions
- 📊 **Live dashboard**: https://lshangro-png.github.io/hindenburg-omen/dashboard_en.html
- 🎛 **Parameter lab**: https://lshangro-png.github.io/hindenburg-omen/lab_en.html (drag sliders, results recompute instantly)
- 📓 **Code**: three Jupyter notebooks (in Chinese), see [Project layout](#project-layout)

Data through 2026-08-07.

## Key findings

On A-shares, the Hindenburg Omen **shows no consistent predictive power**:

- Over a 252-day window, the base rate of a ≥ 10% drawdown is already 85%–100%. Across the five indices,
  hit rate minus base rate ranges from −7 to +9 percentage points — sometimes positive, sometimes negative.
- For the stricter ≥ 20% drawdown, the excess is also inconsistent in sign, and each index has only
  4–14 gradable clusters.
- The 2015 market top produced no signal at all: the market flipped straight from broad rally to broad
  sell-off, so condition ② (many new highs *and* many new lows at the same time) never held on a single day.

## The indicator

A signal fires only on days when all four conditions hold:

| | Condition |
|---|---|
| ① | The 50-day moving average is rising (`MA(t) > MA(t-5)`) |
| ② | The share of stocks at 52-week highs **and** the share at 52-week lows are both ≥ 2.2% |
| ③ | Number of new highs ≤ 2 × number of new lows |
| ④ | The McClellan Oscillator (EMA19 − EMA39 of net advancers) is negative |

Signals that recur at least twice within 36 trading days are upgraded to a **valid cluster**.
For each cluster we measure the maximum drawdown over the next 5 / 21 / 63 / 252 trading days and grade it
by the 252-day drawdown (≤ −20% major drawdown / ≤ −10% moderate pullback / ≤ −5% minor pullback),
which gives the hit rate.

**We also compute the base rate**: the probability of an equally large drawdown within the same window
if you ignore the signal and pick any day at random. On A-shares this is 85%–100% for a 252-day window,
so alongside headline numbers like "92% hit rate" we report **excess = hit rate − base rate**.

Five indices are tested: CSI All Share (中证全指), SSE Composite (上证综指), CSI 300 (沪深300),
CSI 500 (中证500) and CSI 1000 (中证1000). For each, condition ① and the drawdowns use the index itself,
while conditions ②③④ use the matching stock universe (whole market / Shanghai-listed only / index constituents).

## Project layout

The project is three notebooks that pass data to each other through a **PostgreSQL database**:

```
01_数据.ipynb   Data: create tables → one-time import of legacy files → incremental JoinQuant updates
02_回测.ipynb   Backtest: read DB → four conditions → clusters → drawdowns → base rates → results to DB,
                with charts and the full write-up (2015 case study, five-index summary, diagnostics)
03_展示.ipynb   Pages: read results from DB → build the dashboard and parameter lab into docs/
assets/         Front-end files the pages need (chart engine, JS mirror of the backtest, i18n text, CSS)
docs/           GitHub Pages: dashboard and parameter lab, Chinese and English
report/         Research report (Chinese) and its figures
```

| Database table | Written by | Contents |
|---|---|---|
| `stock_close_adj`, `stock_close_unadj`, `stock_state` | 01 | Stock prices (adjusted / unadjusted) and daily trading status |
| `index_close`, `index_meta`, `index_constituents` | 01 | Benchmark index closes and index constituents |
| `breadth` | 02 | Market-breadth series per index (cache, rebuilt when parameters or data change) |
| `daily` | 02 | Per index per day: close, MA, new highs/lows, McClellan, the four conditions, signal |
| `clusters`, `hit_vs_base`, `summary`, `strategy_info`, `index_pre` | 02 | Clusters and drawdowns, hit rate vs. base rate, summary statistics, parameters |

Fields, coverage and example queries for every table are in [数据库说明.md](数据库说明.md) (Chinese). For Tableau, connect directly to the database and use `daily`, `clusters` and `hit_vs_base`.

## Setup

1. Install PostgreSQL and create the database (macOS with Homebrew):

   ```bash
   brew install postgresql@17
   ```

   ```bash
   brew services start postgresql@17
   ```

   ```bash
   /opt/homebrew/opt/postgresql@17/bin/createdb hindenburg
   ```

2. Install the Python packages:

   ```bash
   pip install -r requirements.txt
   ```

3. Run the notebooks in order: `01_数据` → `02_回测` → `03_展示`.

The database address defaults to `postgresql+psycopg://localhost/hindenburg` (no password for a local
connection); set `HO_DB_URL` to use another one.

A JoinQuant account is only needed for updating data. Provide it **only through environment variables —
never put it in a notebook or in this README**:

```bash
export JQ_USER=your_account
export JQ_PASSWORD=your_password
```

On first run, `01_数据` imports the legacy data files (`stock_price_adj_close.pkl`, `stock_state.pkl`,
`index_constituents_*.pkl`, `index_store/*.parquet`) from the folder given by `HO_LEGACY_DIR` /
`HO_LEGACY_INDEX_DIR`; their format is described in [data/README.md](data/README.md).

## Usage

- **New data, same parameters**: run all three notebooks top to bottom. 01 only fetches what is missing.
- **Changed parameters**: edit the `Params` / `STRATEGIES` cell at the top of `02_回测`, then run 02 and 03.
  Breadth series are cached in the database and rebuilt automatically when heavy parameters change.
- **Why did a given day (not) signal?** Section 7.1 of `02_回测`: `inspect_day('2015-06-29')`.
- **Keep vs. exclude ST stocks**: section 7.2 of `02_回测` (set `RUN_COMPARE = True`).

| Variable | Default | Meaning |
|---|---|---|
| `HO_DB_URL` | `postgresql+psycopg://localhost/hindenburg` | Database address |
| `HO_OFFLINE` | empty | Set to `1` to skip all JoinQuant updates |
| `HO_LEGACY_DIR` / `HO_LEGACY_INDEX_DIR` | `data` / `index_store` | Legacy files for the one-time import |
| `HO_PAGES_DIR` | `docs` | Where `03_展示` writes the pages |
| `JQ_RESERVE` / `JQ_MAX_ROWS` / `JQ_MAX_ROWS_PANEL` | 50000 / 20000 / 500000 | JoinQuant quota guards |
| `JQ_DRY_RUN` | empty | Set to `1` to print the fetch plan only |

## FAQ

**"⚠ 尾部 N 个交易日可交易域塌陷" (tradable universe collapsed for the last N days)**: the trading-status
table is behind the prices. Update both together in `01_数据`.

**Indices start on different dates**: JoinQuant has no data before each index's launch date
(CSI All Share only from 2011-08; CSI 300/500/1000 from their own launch dates). Each backtest
simply starts on the first day with data.

**No signal in 2015**: this is a blind spot of the indicator, not a bug. Condition ② needs the shares of
new highs and new lows to both be ≥ 2.2% on the same day. At the mid-June top, new highs were 30%–40% of
stocks while new lows were near zero; once the sell-off began, new highs quickly fell below 1%, and new lows
only surged on July 7–8, by which time condition ① had already failed. Day-by-day data is in section 5.5 of
`02_回测`.

## Methodology choices

All parameters are in the first cell of `02_回测`. Where there is more than one reasonable choice:

| Parameter | Current value | Notes |
|---|---|---|
| `trend_mode` | `ma_rising` | Condition ① uses the **slope of the moving average** (`MA(t) > MA(t-5)`), the original US definition. The alternative `above_ma` (price above its MA, the Zheshang A-share variant) is equally blind to crashes like 2015 |
| `cluster_mode` | `fixed` | The cluster window is **fixed at 36 trading days from the first signal**. `chain` keeps extending the window, which can merge months of signals into one giant cluster |
| `dd_mode` | `peak_trough` | **Peak-to-trough maximum drawdown**, the standard definition. `from_signal` always measures from the starting price |
| `dd_anchor` | `confirm` | Drawdowns are measured from the **cluster confirmation date** (the day of the 2nd signal). The report measures from the first signal, but on that day you don't yet know it will become a valid cluster, which introduces look-ahead bias |
| `mc_norm` | `raw` | McClellan uses the raw advancers-minus-decliners count. `rana` normalises by the number of stocks |
| `state_rule` | `rule_default` | The tradable universe does **not** exclude ST stocks. All five indices use the same rule so results are comparable |

Other numeric parameters:

```
Condition ①   ma_period=50, ma_lookback=5
Condition ②   threshold=0.022 (2.2%)
Condition ③   hl_ratio=2.0
Condition ④   ema_short=19, ema_long=39
Clusters      cluster_window=36, cluster_min=2
Drawdowns     dd_windows=(5,21,63,252), grade_window=252, grade_cuts=(-20%, -10%, -5%)
Highs/lows    hl_window=252, hl_min_periods=200
Other         warmup=260, exclude_boards=('BJ',), min_coverage=0.5
```

Only the SSE Composite sets `include_exchanges=('SH',)` (it contains Shanghai-listed stocks only);
it is empty for the other indices.

## Known limitations

- Each index has only 4–14 gradable valid clusters, so hit-rate confidence intervals are wide.
- The incremental price update only extends stocks **already** in the database; stocks listed later are
  not added automatically. They only enter the tradable universe after a year of trading, so the short-term
  effect is small, but over time the universe gradually misses newer stocks.
- For research only; not investment advice.

## License

[MIT](LICENSE)
