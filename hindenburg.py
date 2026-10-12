"""
命令行入口：把数据层和回测核心串起来，并提供各个子命令。

    python hindenburg.py update-panel   更新价格面板 + 状态表
    python hindenburg.py run            回测并打印结果
    python hindenburg.py viz            生成仪表盘
    python hindenburg.py -h             查看全部子命令

聚宽账号通过环境变量 JQ_USER / JQ_PASSWORD 提供，不要写进代码。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import pandas as pd

import config
from config import Strategy, STRATEGIES
from data import Panel, BreadthCache, IndexStore, LocalTable, new_high_low, mcclellan
from backtest import (build_signals, build_clusters, evaluate, summarize,
                      current_status, signal_day_levels)
import jq_source


# =====================================================================
# 单个策略的回测流程
# =====================================================================

class Omen:
    """
    一个策略的完整回测：compute() 算中间序列和信号 → run() 聚类并评估 → report() 打印。
    """
    def __init__(self, strat: Strategy, panel: Panel, store: IndexStore,
                 cache: Optional[BreadthCache] = None):
        self.s, self.panel, self.store, self.cache = strat, panel, store, cache
        self.stats: Optional[pd.DataFrame] = None
        self.index_full: Optional[pd.Series] = None   # 含预热段的指数收盘价（条件①用）
        self.index_source = strat.benchmark

    def compute(self, start: str, end: str) -> pd.DataFrame:
        """宽度指标 + 基准指数 → 四条件与信号，结果存到 self.stats。"""
        s, p = self.s, self.s.params
        print(f'\n[{s.name}] 计算宽度指标...')
        kw = dict(ema_short=p.ema_short, ema_long=p.ema_long, warmup=p.warmup,
                  hl_window=p.hl_window, hl_min_periods=p.hl_min_periods,
                  mc_norm=p.mc_norm, min_coverage=p.min_coverage,
                  exclude_boards=p.exclude_boards,
                  include_exchanges=p.include_exchanges)
        if self.cache:
            df = self.cache.get(self.panel, start, end, s.universe_index, **kw).copy()
        else:
            px, valid = self.panel.universe(start, end, s.universe_index,
                                            warmup=p.warmup,
                                            min_coverage=p.min_coverage)
            df = new_high_low(px, valid, p.hl_window, p.hl_min_periods)
            df['mcclellan'] = mcclellan(px, valid, p.ema_short, p.ema_long, p.mc_norm)
            df['eq_weight_index'] = Panel.equal_weight_index(px, valid)
            df = df.loc[start:]

        pre_start = (pd.Timestamp(start)
                     - pd.Timedelta(days=int(config.INDEX_PREHEAT * 1.6))).strftime('%Y-%m-%d')
        try:
            full = self.store.get(s.benchmark, pre_start, end).sort_index()
            full = full[~full.index.duplicated(keep='last')]
            self.index_full = full
            df['index_close'] = full.reindex(df.index).ffill()
        except Exception as e:
            print(f'  ⚠ {s.benchmark} 取数失败({e}), 改用等权代理指数, 口径与研报不同')
            df['index_close'] = df['eq_weight_index']
            self.index_full = df['index_close']
            self.index_source = '等权代理'

        # 实际起点 = max(配置的 start, 指数有数据的第一天)
        first = df['index_close'].first_valid_index()
        if first is None:
            raise ValueError(f'{s.benchmark} 在 {start}~{end} 内没有任何指数数据')
        if first > df.index[0]:
            df = df.loc[first:]

        df = build_signals(df, p, index_full=self.index_full)
        self.stats = df
        return df

    def run(self) -> pd.DataFrame:
        """聚类并评估，返回集群表。"""
        cl = build_clusters(self.stats, self.s.params)
        if not cl.empty:
            nv = (cl.type == '有效集群').sum()
            print(f'[{self.s.name}] {len(cl)} 个事件 ({nv} 有效集群 / {len(cl)-nv} 单点)')
        else:
            print(f'[{self.s.name}] 无信号')
        return evaluate(self.stats, cl, self.s.params)

    def report(self, cl: pd.DataFrame):
        """在终端打印当前状态、集群列表、命中率与基准率。"""
        s, p = self.s, self.s.params
        print(f'\n{"="*74}\n  {s.name} 回测结果  (趋势基准: {self.index_source})\n{"="*74}')

        st = current_status(self.stats, cl, p)
        print(f'\n当前状态 (截至 {st["as_of"]})')
        print(f'  最近信号日: {st["last_signal_date"] or "无"}')
        print(f'  近 {st["window"]} 日单点信号: {st["recent_signal_count"]} 次')
        ac = st['active_cluster']
        if ac:
            confirm_txt = f'已于 {ac["confirm_date"]} 确认' if ac['confirm_date'] else '尚未确认'
            print(f'  近期集群: {ac["start"]} ~ {ac["end"]}  {ac["count"]} 次信号  '
                  f'{ac["type"]} ({confirm_txt})')
        else:
            print(f'  近 {st["window"]} 日内无集群')

        if cl.empty:
            print('\n  无信号')
            return
        m = summarize(self.stats, cl, p)
        valid = cl[cl.type == '有效集群']
        print(f'\n集群总数 {m["n_clusters_valid"]} 个 '
              f'(含 {m["n_clusters_valid"]-m["n_gradable"]} 个近期信号)\n')
        anchor_cn = {'confirm': '集群确认日', 'first': '集群首日'}[p.dd_anchor]
        print(f'回撤起算点: {anchor_cn}   回撤口径: '
              f'{"窗口内峰谷" if p.dd_mode == "peak_trough" else "锚点日收盘起"}\n')
        print(f'{"ID":<4}{"预警区间":<26}{"锚点日":<13}{"特征数":<7}{"回撤":<9}等级')
        print('-' * 74)
        for _, r in valid.iterrows():
            dd = r[f'dd_{p.grade_window}d']
            print(f"{r.cluster_id:<4}{r.start_date:%Y-%m-%d} ~ {r.end_date:%Y-%m-%d}  "
                  f"{r.anchor_date:%Y-%m-%d}   {r.signal_count:<7}"
                  f"{'观察期未满' if pd.isna(dd) else f'{dd:.1%}':<9}{r.level}")
        if not m['n_gradable']:
            return

        gw, ng = p.grade_window, m['n_gradable']
        ba = m['baseline_all'].get(gw)
        print(f'\n中型回撤命中率: {m["hit_rate"]:.0%}  较大回撤命中率: '
              f'{m["hit_big"]/ng:.0%}  (基于 {ng} 个可评估集群)')
        if ba:
            print(f'同期基准率({gw}日窗口, 全样本随机一天): 中型 {ba["p_mid"]:.0%}  '
                  f'较大 {ba["p_big"]:.0%}')
            if ba['p_mid'] > 0.9:
                print(f'  ⚠ 基准率已达 {ba["p_mid"]:.0%}, {gw} 日窗口在本市场几乎必然'
                      f'出现该级别回撤, 命中率无区分度。')


# =====================================================================
# 公用小工具
# =====================================================================

def _end_date(panel: Panel) -> str:
    """回测终点：HO_END，否则为面板最后一天；联网时顺便提醒面板是否落后。"""
    if config.END:
        return config.END
    local_end = panel.last_date
    try:
        if not config.OFFLINE:
            src = jq_source.make_source()
            if src is not None:
                real = jq_source.latest_trade_day()
                behind = len(pd.bdate_range(local_end, real)) - 1
                if behind > 0:
                    print(f'⚠ 本地面板到 {local_end:%Y-%m-%d}, 市场已到 {real:%Y-%m-%d} '
                          f'(约 {behind} 个交易日未更新), 先跑 `python hindenburg.py update-panel`')
    except Exception:
        pass          # 只是提醒，失败不影响回测
    return local_end.strftime('%Y-%m-%d')


def _make_store(allow_remote: bool = True) -> IndexStore:
    return IndexStore(source=jq_source.make_source(allow_remote),
                      allow_remote=allow_remote)


def _prepare(key: str, store=None, cache=None):
    """按策略键建好 Omen 并完成 compute()。"""
    s = STRATEGIES[key]
    panel = Panel(state_rule=s.state_rule,
                  exclude_boards=s.params.exclude_boards,
                  include_exchanges=s.params.include_exchanges)
    end = _end_date(panel)
    start = s.start or config.START
    omen = Omen(s, panel, store or _make_store(), cache or BreadthCache())
    omen.compute(start, end)
    return omen


# =====================================================================
# 导出
# =====================================================================

def _index_pre(omen: Omen, df: pd.DataFrame) -> dict:
    """回测起点之前的指数收盘价（供 backtest.js 计算条件①的均线）。"""
    full = omen.index_full
    if full is None:
        return {'dates': [], 'index_close': []}
    pre = full[full.index < df.index[0]].tail(config.INDEX_PREHEAT)
    return {'dates': [str(d.date()) for d in pre.index],
            'index_close': [None if pd.isna(v) else round(float(v), 3) for v in pre]}


def export_payload(omen: Omen, cl: pd.DataFrame) -> dict:
    """
    导出给仪表盘 / 参数实验室的 JSON。

    除了结果，还带上中间序列（series），参数实验室靠它们在浏览器里重算轻参数。
    """
    df, p = omen.stats, omen.s.params
    m = summarize(df, cl, p)

    def clean(v):
        return None if (v is None or (isinstance(v, float) and pd.isna(v))) else v

    def col(name, nd=None, as_int=False):
        out = []
        for v in df[name]:
            if pd.isna(v):
                out.append(None)
            elif as_int:
                out.append(int(v))
            else:
                out.append(round(float(v), nd) if nd is not None else float(v))
        return out

    return {
        'strategy': omen.s.name,
        'benchmark': omen.s.benchmark,
        'index_source': omen.index_source,
        'state_rule': omen.s.state_rule,
        'params': {**p.light(), **p.heavy()},
        # 参数实验室据此把重参数显示为只读
        'heavy_keys': sorted(config.HEAVY_KEYS),
        'meta': {'start': str(df.index[0].date()), 'end': str(df.index[-1].date()), **m},
        'signal_days': signal_day_levels(df, cl, p),
        'index_pre': _index_pre(omen, df),
        'series': {
            'dates': [str(d.date()) for d in df.index],
            'index_close': col('index_close', 3),
            'high_ratio': col('high_ratio', 5),
            'low_ratio': col('low_ratio', 5),
            'new_high': col('new_high', as_int=True),
            'new_low': col('new_low', as_int=True),
            'mcclellan': col('mcclellan', 2),
        },
        'clusters': [
            {'id': int(r.cluster_id), 'start': str(r.start_date.date()),
             'end': str(r.end_date.date()), 'count': int(r.signal_count),
             'confirm': (None if pd.isna(r.confirm_date)
                         else str(r.confirm_date.date())),
             'anchor': str(r.anchor_date.date()),
             'type': r.type, 'level': r.level,
             **{f'dd{w}': clean(None if pd.isna(getattr(r, f'dd_{w}d'))
                                else round(float(getattr(r, f'dd_{w}d')), 4))
                for w in p.dd_windows}}
            for r in cl.itertuples()
        ] if not cl.empty else [],
    }


# =====================================================================
# 子命令
# =====================================================================

def cmd_run(strategy: Optional[str]):
    """回测并打印结果，集群表写到 OUT_DIR/clusters_<策略>.csv。"""
    keys = [strategy] if strategy else list(STRATEGIES)
    store, cache = _make_store(), BreadthCache()
    failed = []
    for k in keys:
        try:
            omen = _prepare(k, store, cache)
            cl = omen.run()
            omen.report(cl)
            if not cl.empty:
                fn = Path(config.OUT_DIR) / f'clusters_{k}.csv'
                fn.parent.mkdir(parents=True, exist_ok=True)
                cl.to_csv(fn, index=False)
                print(f'\n已导出 {fn}')
        except Exception as e:
            failed.append((k, e))
            print(f'\n⚠ 策略 {k} ({STRATEGIES[k].name}) 失败, 已跳过: '
                  f'{type(e).__name__}: {e}\n')
    if failed:
        print(f'\n{"="*74}\n{len(failed)}/{len(keys)} 个策略失败: '
              + ', '.join(f'{k}({STRATEGIES[k].name})' for k, _ in failed))


def cmd_export(strategy: Optional[str], out_dir: str):
    """每个策略导出一份 export_<策略>.json；失败的策略会删掉旧文件，避免混淆。"""
    keys = [strategy] if strategy else list(STRATEGIES)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    store, cache = _make_store(), BreadthCache()
    paths, failed = [], []
    for k in keys:
        p = Path(out_dir) / f'export_{k}.json'
        try:
            omen = _prepare(k, store, cache)
            payload = export_payload(omen, omen.run())
            p.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            print(f'已导出 {p} ({p.stat().st_size/1024:.0f} KB)')
            paths.append(p)
        except Exception as e:
            failed.append((k, e))
            print(f'⚠ 策略 {k} ({STRATEGIES[k].name}) 导出失败, 已跳过: '
                  f'{type(e).__name__}: {e}')
            if p.exists():
                p.unlink()
                print(f'  (已删除陈旧的 {p.name}, 避免和新结果混淆)')
    if failed:
        print(f'\n{len(failed)}/{len(keys)} 个策略导出失败: '
              + ', '.join(f'{k}({STRATEGIES[k].name})' for k, _ in failed))
    return paths


def cmd_viz(out_dir: str, reuse: bool):
    """生成 dashboard.html；默认先重新 export，--reuse 则直接用已有 JSON。"""
    import viz
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if not reuse:
        cmd_export(None, out_dir)
    payloads = {}
    for k in STRATEGIES:
        p = out / f'export_{k}.json'
        if p.exists():
            payloads[k] = json.loads(p.read_text(encoding='utf-8'))
    if not payloads:
        raise SystemExit(f'{out} 下没有 export_*.json, 先跑 `python hindenburg.py export`')
    files = viz.build_all(payloads, out)
    for f in files:
        print(f'已生成 {f} ({f.stat().st_size/1024:.0f} KB)')


def cmd_update(reset_head: Optional[str] = None):
    """
    更新各策略基准指数的收盘价。

    reset_head: 清除该指数（或 'all'）的 head_floor 记录，重新尝试拉取更早的历史。
    """
    panel = Panel()
    end = _end_date(panel)
    store = _make_store()

    if reset_head:
        targets = ({s.benchmark for s in STRATEGIES.values()}
                   if reset_head == 'all' else {reset_head})
        for code in targets:
            p = store._meta_path(code)
            if p.exists():
                p.unlink()
                print(f'已清除 {code} 的 head_floor 记录, 本次会重新尝试拉取历史')

    starts = {}
    for s in STRATEGIES.values():
        cur = s.start or config.START
        if s.benchmark not in starts or cur < starts[s.benchmark]:
            starts[s.benchmark] = cur

    for code, st in sorted(starts.items()):
        try:
            ser = store.get(code, st, end)
            print(f'  {code}: {len(ser)} 行, {ser.index.min():%Y-%m-%d} ~ '
                  f'{ser.index.max():%Y-%m-%d}\n')
        except Exception as e:
            print(f'  {code} 更新失败: {e}\n')
    print(store.list_stored().to_string(index=False))


def cmd_update_panel(end: Optional[str], dry_run: bool):
    """价格面板和状态表一起增量更新（两者必须同步，否则尾部交易日会被截掉）。"""
    if config.OFFLINE:
        raise SystemExit('HO_OFFLINE=1 时不联网, 无法更新')
    panel = Panel()
    px_path = panel.find('stock_price_adj_close.pkl')
    if px_path is None:
        raise SystemExit(f'{config.DATA_DIR} 下找不到 stock_price_adj_close.pkl')

    print('=== 价格面板 ===')
    jq_source.update_price_panel(px_path, end, dry_run, local_table=LocalTable())

    st_path = panel.find('stock_state.pkl')
    print('\n=== 状态表 ===')
    if st_path is None:
        print('未找到 stock_state.pkl, 跳过(将退化为价格推断可交易域)')
        return
    try:
        jq_source.update_state_table(st_path, end, dry_run)
    except Exception as e:
        print(f'⚠ 状态表更新失败: {type(e).__name__}: {e}')
        print('  价格面板已更新成功。状态表落后会让尾部交易日被截断, '
              '可稍后重新运行 update-panel 重试。')


def cmd_fetch_constituents(index_code: str, start: Optional[str], end: Optional[str]):
    """从聚宽拉取指数成分股。"""
    if config.OFFLINE:
        raise SystemExit('HO_OFFLINE=1 时不联网')
    s = next((st for st in STRATEGIES.values() if st.universe_index == index_code), None)
    start = start or (s.start if s else config.START)
    end = end or _end_date(Panel())
    jq_source.fetch_constituents(index_code, start, str(end))


def cmd_quota():
    try:
        print(jq_source.quota_str())
    except Exception as e:
        print(f'查询失败: {e}')


def cmd_stored():
    df = _make_store(allow_remote=False).list_stored()
    print(df.to_string(index=False) if len(df) else '本地还没有任何指数 parquet')


def cmd_clear_cache():
    BreadthCache().clear()


def cmd_inspect():
    """打印状态表各字段的取值分布。"""
    p = Panel().find('stock_state.pkl')
    if p is None:
        raise SystemExit(f'{config.DATA_DIR} 下找不到 stock_state.pkl')
    raw = pd.read_pickle(p)
    print(f'文件: {p}\n形状: {raw.shape}\n索引: {raw.index.names}')
    if isinstance(raw.index, pd.MultiIndex):
        d = raw.index.get_level_values(0)
        print(f'日期: {d.min()} ~ {d.max()} ({d.nunique()} 个交易日), '
              f'股票 {raw.index.get_level_values(1).nunique()} 只')
    for c in raw.columns:
        s = raw[c]
        print(f'\n【{c}】 dtype={s.dtype} 缺失={s.isna().mean():.1%}')
        vc = s.value_counts(dropna=True)
        if len(vc) <= 12:
            for v, n in vc.items():
                print(f'    {v!r:>14} : {n:>12,} ({n/len(s):>6.1%})')
        else:
            print(f'    连续值 {len(vc)} 种, min={s.min()} 中位={s.median()} max={s.max()}')


def cmd_inspect_day(date: str, strategy: str, days: int):
    """打印某天前后几天四个条件的原始值，用来诊断为什么触发 / 没触发。"""
    omen = _prepare(strategy)
    df = omen.stats
    p = omen.s.params
    target = pd.Timestamp(date)

    if target < df.index[0] or target > df.index[-1]:
        print(f'{target.date()} 超出该策略的回测区间 [{df.index[0].date()}, '
              f'{df.index[-1].date()}], 检查是否用错了 --strategy。')
        return
    if target not in df.index:
        pos = df.index.searchsorted(target)
        nearest = df.index[min(pos, len(df) - 1)]
        print(f'{target.date()} 不是交易日(或不在数据里), 最近的交易日是 {nearest.date()}。')
        target = nearest

    lo = df.index.searchsorted(target - pd.Timedelta(days=days))
    hi = df.index.searchsorted(target + pd.Timedelta(days=days), side='right')
    window = df.iloc[lo:hi]

    print(f'\n[{omen.s.name}] {target.date()} 前后 {days} 天, '
          f'四条件原始值 (阈值: threshold={p.threshold:.1%}, hl_ratio={p.hl_ratio}, '
          f'ma_period={p.ma_period}, ma_lookback={p.ma_lookback})\n')
    header = (f'{"日期":<12}{"收盘":>9}{"①MA上升":>9}{"②新高%":>8}{"②新低%":>8}'
              f'{"③新高≤2x":>9}{"④MC":>8}{"signal":>8}')
    print(header)
    print('-' * len(header))
    for d, r in window.iterrows():
        mark = ' <==' if d == target else ''
        print(f'{d.date()!s:<12}{r.index_close:>9.1f}'
              f'{str(bool(r.cond1)):>9}{r.high_ratio:>8.2%}{r.low_ratio:>8.2%}'
              f'{str(bool(r.cond3)):>9}{r.mcclellan:>8.1f}'
              f'{str(bool(r.signal)):>8}{mark}')

    row = df.loc[target]
    failed = [f'条件{i}' for i, ok in enumerate(
        [row.cond1, row.cond2, row.cond3, row.cond4], 1) if not ok]
    if row.signal:
        print(f'\n{target.date()} 四条件全部满足, 是信号日。')
    else:
        print(f'\n{target.date()} 未触发信号, 未满足: {", ".join(failed) or "无(检查逻辑)"}')
    print('若这天在参考报告里被标记而这里没有, 上面这行原始数据就是唯一的分歧来源 ——'
          '不是四舍五入就是数据源(可交易域/复权方式/指数口径)有差异, 不会是随机的。')


def cmd_compare():
    """用中证全指对比三种可交易域规则（不剔 ST / 剔 ST / 按 trade_status）下的信号差异。"""
    panel0 = Panel()
    if panel0.find('stock_state.pkl') is None:
        print('⚠ 无 stock_state.pkl, 三条规则退化为同一条(价格推断), 对比无意义')
        return
    end = _end_date(panel0)
    store = _make_store()
    s0 = STRATEGIES['ashare']
    try:
        bench = store.get(s0.benchmark, config.START, end)
    except Exception as e:
        bench = None
        print(f'⚠ 未取到基准指数 ({e}), 用等权代理, 结果与回测不可比\n')

    p = s0.params
    res = []
    for label, rule in [('基准(不剔ST)', 'rule_default'), ('剔除ST/*ST', 'rule_strict'),
                        ('按trade_status', 'rule_trade_status')]:
        print(f'\n{"-"*60}\n{label}\n{"-"*60}')
        px, valid = Panel(state_rule=rule,
                          exclude_boards=p.exclude_boards,
                  include_exchanges=p.include_exchanges).universe(
                              config.START, end, warmup=p.warmup,
                              min_coverage=p.min_coverage)
        bd = new_high_low(px, valid, p.hl_window, p.hl_min_periods)
        idx = (bench.reindex(px.index).ffill() if bench is not None
               else Panel.equal_weight_index(px, valid))
        ma = idx.rolling(p.ma_period).mean()
        c2 = (bd.high_ratio >= p.threshold) & (bd.low_ratio >= p.threshold)
        sig = ((ma > ma.shift(p.ma_lookback)) & c2
               & (bd.new_high <= bd.new_low * p.hl_ratio)
               & (mcclellan(px, valid, p.ema_short, p.ema_long, p.mc_norm) < 0))
        sig = sig.loc[config.START:]
        res.append({'label': label, 'n': valid.sum(axis=1).mean(),
                    'lo': bd.low_ratio.median(), 'c2': int(c2.loc[config.START:].sum()),
                    'sig': int(sig.sum()), 'dates': set(sig[sig].index)})

    print(f'\n\n{"="*70}\n规则对比\n{"="*70}')
    print(f'\n{"规则":<16}{"日均只数":>9}{"新低中位":>10}{"条件②":>8}{"信号":>7}')
    print('-' * 70)
    for r in res:
        print(f"{r['label']:<16}{r['n']:>9.0f}{r['lo']:>10.2%}{r['c2']:>8}{r['sig']:>7}")
    base = res[0]
    print(f'\n与「{base["label"]}」的逐日差异 (比看总数可靠):')
    for r in res[1:]:
        print(f"  {r['label']:<16}共同 {len(base['dates'] & r['dates']):>3} | "
              f"仅基准 {len(base['dates'] - r['dates']):>3} | "
              f"仅本规则 {len(r['dates'] - base['dates']):>3}")


# =====================================================================
# 命令行
# =====================================================================

def main():
    ap = argparse.ArgumentParser(description='兴登堡预兆 A股复现')
    sub = ap.add_subparsers(dest='cmd')

    p_run = sub.add_parser('run', help='回测 (默认命令)')
    p_run.add_argument('--strategy', choices=list(STRATEGIES), default=None)

    p_exp = sub.add_parser('export', help='导出可视化 JSON')
    p_exp.add_argument('--strategy', choices=list(STRATEGIES), default=None)
    p_exp.add_argument('--out-dir', default=config.OUT_DIR)

    p_viz = sub.add_parser('viz', help='生成仪表盘 HTML (dashboard.html)')
    p_viz.add_argument('--out-dir', default=config.OUT_DIR)
    p_viz.add_argument('--reuse', action='store_true', help='直接用已有 JSON, 不重跑回测')

    p_upd = sub.add_parser('update', help='更新指数收盘')
    p_upd.add_argument('--reset-head', default=None, metavar='CODE',
                       help="清除该指数的 head_floor 记录后重试历史拉取, 'all'=全部")

    p_fc = sub.add_parser('fetch-constituents', help='从聚宽拉指数成分股')
    p_fc.add_argument('index_code', help='如 000905.SH')
    p_fc.add_argument('--start', default=None)
    p_fc.add_argument('--end', default=None)

    p_day = sub.add_parser('inspect-day', help='诊断某天为什么触发/没触发信号')
    p_day.add_argument('date', help='YYYY-MM-DD')
    p_day.add_argument('--strategy', choices=list(STRATEGIES), default='ashare')
    p_day.add_argument('--days', type=int, default=8, help='前后各看多少天, 默认8')

    p_up = sub.add_parser('update-panel', help='增量更新股票价格面板')
    p_up.add_argument('--end', default=None)
    p_up.add_argument('--dry-run', action='store_true')

    for name, helptext in [('quota', '查看聚宽额度'),
                           ('stored', '查看本地已缓存指数'), ('inspect', '诊断状态表'),
                           ('compare', '对比可交易域规则'), ('clear-cache', '清空宽度缓存')]:
        sub.add_parser(name, help=helptext)

    a = ap.parse_args()
    cmd = a.cmd or 'run'
    if cmd == 'run':
        cmd_run(getattr(a, 'strategy', None))
    elif cmd == 'export':
        cmd_export(a.strategy, a.out_dir)
    elif cmd == 'viz':
        cmd_viz(a.out_dir, a.reuse)
    elif cmd == 'update':
        cmd_update(a.reset_head)
    elif cmd == 'update-panel':
        cmd_update_panel(a.end, a.dry_run)
    elif cmd == 'quota':
        cmd_quota()
    elif cmd == 'stored':
        cmd_stored()
    elif cmd == 'inspect':
        cmd_inspect()
    elif cmd == 'fetch-constituents':
        cmd_fetch_constituents(a.index_code, a.start, a.end)
    elif cmd == 'inspect-day':
        cmd_inspect_day(a.date, a.strategy, a.days)
    elif cmd == 'compare':
        cmd_compare()
    elif cmd == 'clear-cache':
        cmd_clear_cache()


if __name__ == '__main__':
    main()
