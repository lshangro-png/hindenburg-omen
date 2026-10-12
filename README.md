# Hindenburg Omen — A Reproduction on China A-Shares

**English** | [中文](README.zh-CN.md)

A reproduction of the Hindenburg Omen, a market-top warning indicator, on China's A-share market,
tested against **base rates** to check whether it carries any real information.
Reference: Zheshang Securities, *How effective is the "Hindenburg Omen" at avoiding index declines?
— Chip Microstructure Series (IX)* (2026-06-26, in Chinese).

- 📓 **Start here**: [walkthrough.ipynb](walkthrough.ipynb) — the full research process and conclusions, with charts (currently in Chinese; an English version is in progress)
- 📊 **Live dashboard**: https://lshangro-png.github.io/hindenburg-omen/
- 🎛 **Parameter lab**: https://lshangro-png.github.io/hindenburg-omen/lab.html (drag sliders, results recompute instantly)

Data through 2026-08-07.

## Key findings

On A-shares, the Hindenburg Omen **shows no consistent predictive power**:

- Over a 252-day window, the base rate of a ≥ 10% drawdown is already 85%–100%. Across the five indices,
  hit rate minus base rate ranges from −7 to +9 percentage points — sometimes positive, sometimes negative.
- For the stricter ≥ 20% drawdown, the excess is also inconsistent in sign, and each index has only
  4–14 gradable clusters.
- The 2015 market top produced no signal at all: the market flipped straight from broad rally to broad
  sell-off, so condition ② (many new highs *and* many new lows at the same time) never held on a single day.

See [walkthrough.ipynb](walkthrough.ipynb) for the full analysis.

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

```
.
├── walkthrough.ipynb  Research walkthrough: calls the modules below, from data to conclusions
├── hindenburg.py      Command-line entry point (update / run / viz / inspect-day ...)
├── backtest.py        Pure backtest functions: build_signals → build_clusters → evaluate → summarize
├── config.py          All parameters and strategy definitions (light vs. heavy parameters)
├── data.py            Data layer: price panel, tradable universe, new highs/lows, McClellan, local caches
├── jq_source.py       JoinQuant data source (the only remote source), with quota guards and incremental updates
├── viz.py             Builds the backtest dashboard, dashboard.html
├── lab.py             Builds the parameter lab, lab.html (recomputes light parameters in the browser)
├── export_unadj.py    Experimental comparison: unadjusted prices + unfiltered universe (lab only)
├── assets/
│   ├── backtest.js    JS mirror of backtest.py, function by function
│   ├── common.js      Front-end helpers shared by the dashboard and the lab
│   ├── chart_engine.js  Lightweight SVG chart engine
│   └── report.css
├── docs/              GitHub Pages: snapshots of the dashboard and the lab
├── data/              Local data (not committed; format in data/README.md)
└── output/            Run outputs: clusters_*.csv / export_*.json / *.html (not committed)
```

Runs also create `.cache/` (breadth-indicator cache) and `index_store/` (index close prices as parquet);
neither is committed.

## Installation

```bash
pip install -r requirements.txt
```

See [data/README.md](data/README.md) for the expected data files.

A JoinQuant account is optional; without one the project runs in local-only mode. Provide credentials
**only through environment variables — never put them in code or in this README**:

```bash
export JQ_USER=your_account
export JQ_PASSWORD=your_password
```

## Usage

**Routine update (new data, unchanged parameters):**

```bash
python hindenburg.py update-panel    # bring the price panel and state table up to the latest trading day
python hindenburg.py run             # run the backtest, print results, write output/clusters_*.csv
python hindenburg.py viz             # build output/dashboard.html
```

No need for `clear-cache`: the cache key includes the data files' modification times, so the cache
invalidates itself whenever the data changes.

**After changing parameters in `config.py`:**

```bash
python hindenburg.py clear-cache
python hindenburg.py run
python hindenburg.py viz
```

**Run a single index:**

```bash
python hindenburg.py run --strategy ashare
# options: ashare (CSI All Share) / general (SSE Composite) / 300 / 500 / 1000
```

**Rebuild the page without re-running the backtest:**

```bash
python hindenburg.py viz --reuse     # reuse the last exported JSON
```

**Parameter lab** (drag sliders, see results without re-running):

```bash
python hindenburg.py export
python lab.py                        # builds output/lab.html
```

**Experimental "unadjusted prices + no filtering" comparison** (shown only in the parameter lab, not in
the dashboard; this is not the project's official methodology, just a point of comparison):

```bash
python export_unadj.py data
python lab.py
```

**Find out why a given day did or didn't signal:**

```bash
python hindenburg.py inspect-day 2015-06-29 --strategy ashare
```

This prints the raw values of all four conditions around that day, so you can see which one failed.

**Other commands:**

```bash
python hindenburg.py quota           # remaining JoinQuant quota
python hindenburg.py stored          # which indices are cached locally
python hindenburg.py inspect         # value distribution of the state-table fields
python hindenburg.py compare         # effect of excluding vs. keeping ST stocks
python hindenburg.py update --reset-head 000985.CSI   # force a retry of an index's early history
```

**Environment variables:**

| Variable | Default | Meaning |
|---|---|---|
| `HO_DATA_DIR` | `data` | Local panel data directory |
| `HO_OUT_DIR` | `output` | Output directory |
| `HO_CACHE_DIR` / `HO_INDEX_DIR` | `.cache` / `index_store` | Cache directories |
| `HO_OFFLINE` | empty | Set to `1` to force local-only mode (no network) |
| `HO_START` / `HO_END` | `2000-01-01` / last panel date | Default backtest range |
| `JQ_RESERVE` / `JQ_MAX_ROWS` / `JQ_MAX_ROWS_PANEL` | 50000 / 20000 / 500000 | JoinQuant quota guards |
| `JQ_DRY_RUN` | empty | Set to `1` to print the fetch plan only |

## FAQ

**"⚠ 尾部 N 个交易日可交易域塌陷" (tradable universe collapsed for the last N days)**: `stock_state.pkl`
is behind the price panel. Run `python hindenburg.py update-panel`, which updates both files together.

**Indices start on different dates**: JoinQuant has no data before each index's launch date
(CSI All Share only from 2011-08; CSI 300/500/1000 from their own launch dates). Each backtest
simply starts on the first day with data.

**No signal in 2015**: this is a blind spot of the indicator, not a bug. Condition ② needs the shares of
new highs and new lows to both be ≥ 2.2% on the same day. At the mid-June top, new highs were 30%–40% of
stocks while new lows were near zero; once the sell-off began, new highs quickly fell below 1%, and new lows
only surged on July 7–8, by which time condition ① had already failed. The market went straight from broad
rally to broad sell-off, and condition ② never held on a single day. Day-by-day data is in section 6 of
walkthrough.ipynb.

## Methodology choices

All parameters live in `config.py`. Where there is more than one reasonable choice:

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
- `update-panel` only extends stocks **already** in the panel; stocks listed after the panel was built are
  not added automatically. They only enter the tradable universe after a year of trading, so the short-term
  effect is small, but without rebuilding the panel the universe gradually misses newer stocks.
- For research only; not investment advice.

## License

[MIT](LICENSE)
