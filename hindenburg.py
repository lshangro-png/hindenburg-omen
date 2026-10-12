"""
兴登堡预兆 (Hindenburg Omen) —— A股复现, 命令行入口

聚宽账号走环境变量 (见 .env.example), 不要写进代码:
    export JQ_USER=...
    export JQ_PASSWORD=...

    python hindenburg.py update-panel
    python hindenburg.py run
    python hindenburg.py viz
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import config
from config import Params, Strategy, STRATEGIES
from data import Panel, BreadthCache, IndexStore, LocalTable, new_high_low, mcclellan
import jq_source


# =====================================================================
# 回测核心 —— 全部写成纯函数, 输入中间序列输出结果。
# 这样同一套逻辑可以在 viz.py 生成的 JS 里逐行镜像, 参数实验室才能
# 在浏览器里算出跟 Python 一致的结果。
# =====================================================================

def build_signals(df: pd.DataFrame, p: Params,
                  index_full: Optional[pd.Series] = None) -> pd.DataFrame:
    """
    由中间序列 + 轻参数算出四条件与信号。df 需含 index_close/high_ratio/
    low_ratio/new_high/new_low/mcclellan。

    index_full: 带预热段的指数收盘序列(索引可早于 df.index)。
    """
    src = df['index_close'] if index_full is None else index_full
    ma = src.rolling(p.ma_period).mean()
    if p.trend_mode == 'ma_rising':
        # 美股原始定义: 均线本身的斜率, MA(t) > MA(t - ma_lookback)
        cond1 = ma > ma.shift(p.ma_lookback)
    elif p.trend_mode == 'above_ma':
        # 浙商 A股适配版口径: 价格相对均线的位置, Close(t) > MA(t)
        cond1 = src > ma
    else:
        raise ValueError(f'trend_mode 只能是 ma_rising 或 above_ma, 收到 {p.trend_mode!r}')
    cond1 = cond1.reindex(df.index).astype('boolean').fillna(False).astype(bool)
    out = df.copy()
    out['cond1'] = cond1
    out['cond2'] = (df['high_ratio'] >= p.threshold) & (df['low_ratio'] >= p.threshold)
    out['cond3'] = df['new_high'] <= df['new_low'] * p.hl_ratio
    out['cond4'] = df['mcclellan'] < 0
    out['signal'] = out.cond1 & out.cond2 & out.cond3 & out.cond4
    return out


def build_clusters(df: pd.DataFrame, p: Params) -> pd.DataFrame:
    """
    信号聚类, 两种模式:

      fixed  窗口从集群首日固定往后 cluster_window 个交易日。集群跨度上限
             就是 cluster_window。
      chain  链式延展: 每来一个新信号, 就把窗口从该信号重新往后延
             cluster_window 天。只要信号间隔不超过窗口就一直串下去,
             集群跨度可以远大于 cluster_window。
    """
    dates = list(df.index[df.signal])
    if not dates:
        return pd.DataFrame()
    pos = {d: i for i, d in enumerate(df.index)}
    out = []

    if p.cluster_mode == 'chain':
        group = [dates[0]]
        for prev, cur in zip(dates, dates[1:]):
            if pos[cur] - pos[prev] <= p.cluster_window:
                group.append(cur)
            else:
                out.append(group)
                group = [cur]
        out.append(group)
    elif p.cluster_mode == 'fixed':
        used = set()
        for d in dates:
            if d in used:
                continue
            end_d = df.index[min(pos[d] + p.cluster_window, len(df) - 1)]
            members = [x for x in dates if d <= x <= end_d and x not in used]
            used.update(members)
            out.append(members)
    else:
        raise ValueError(f'cluster_mode 只能是 fixed 或 chain, 收到 {p.cluster_mode!r}')

    return pd.DataFrame([
        {'cluster_id': i + 1, 'start_date': g[0], 'end_date': g[-1],
         # 确认日 = 第 cluster_min 个信号出现的那天。集群"是不是有效集群"要等到
         # 这天才知道, 所以这天才是实盘里能采取行动的最早时点。单点集群没有
         # 确认日 (它永远没升级)。
         'confirm_date': g[p.cluster_min - 1] if len(g) >= p.cluster_min else pd.NaT,
         'signal_count': len(g),
         'type': '有效集群' if len(g) >= p.cluster_min else '单点'}
        for i, g in enumerate(out)])


def grade(dd, cuts) -> str:
    if dd is None or pd.isna(dd):
        return '近期信号'   # 研报用词: 距最新数据不足观察窗口, 等级待定
    big, mid, small = cuts
    return ('较大回撤' if dd <= big else '中型回调' if dd <= mid
            else '小幅回调' if dd <= small else '无显著回撤')


def forward_drawdown(close: pd.Series, s: int, w: int, mode: str) -> float:
    """
    从位置 s 起, 未来 w 天(含)的回撤。被 evaluate/baseline_rates/signal_day_levels
    共用, 三处口径必须完全一致, 所以抽成一个函数, 不要各自实现一份。

      peak_trough  窗口内峰谷最大回撤(允许先涨到高点再从高点起算, 默认)
      from_signal  相对起点收盘价的最低点跌幅
    """
    n = len(close)
    if s + w >= n:
        return np.nan
    seg = close.iloc[s:s + w + 1]
    if mode == 'peak_trough':
        return float((seg / seg.cummax() - 1).min())
    return float(seg.min() / close.iloc[s] - 1)


def evaluate(df: pd.DataFrame, cl: pd.DataFrame, p: Params) -> pd.DataFrame:
    """
    两个正交的口径开关:

    dd_mode   回撤怎么算
        peak_trough  窗口内峰谷最大回撤(默认, 研报口径)
        from_signal  相对锚点日收盘价的最低点跌幅

    dd_anchor 从哪天起算
        confirm  集群确认日, 即第 cluster_min 个信号出现那天(默认)
        first    集群首日(研报口径)

    单点集群没有确认日, 无论 dd_anchor 取什么都退回首日 —— 它们本来也不进统计。
    """
    if cl.empty:
        return cl
    close = df['index_close']
    pos = {d: i for i, d in enumerate(df.index)}

    def anchor_of(r) -> pd.Timestamp:
        if p.dd_anchor == 'first':
            return r.start_date
        if p.dd_anchor != 'confirm':
            raise ValueError(f'dd_anchor 只能是 confirm 或 first, 收到 {p.dd_anchor!r}')
        return r.start_date if pd.isna(r.confirm_date) else r.confirm_date

    anchors = [anchor_of(r) for _, r in cl.iterrows()]
    cl = cl.copy()
    cl['anchor_date'] = anchors
    cols = {f'dd_{w}d': [forward_drawdown(close, pos[a], w, p.dd_mode) for a in anchors]
            for w in p.dd_windows}
    cl = pd.concat([cl, pd.DataFrame(cols, index=cl.index)], axis=1)
    cl['level'] = cl[f'dd_{p.grade_window}d'].map(lambda d: grade(d, p.grade_cuts))
    return cl


def baseline_rates(df: pd.DataFrame, p: Params, mask: Optional[pd.Series] = None) -> dict:
    """
    无条件对照组: 把样本里每一天都当作信号日, 算同样口径的回撤分布。

    mask: 限定抽样日集合。传 cond1 就得到"只在趋势向上时随机挑一天"的基准,
    用来剥离趋势过滤本身的贡献, 看条件②③④ 还剩多少增量。
    """
    close = df['index_close']
    n = len(close)
    idx = np.arange(n) if mask is None else np.flatnonzero(mask.values)
    out = {}
    for w in p.dd_windows:
        vals = [forward_drawdown(close, s, w, p.dd_mode) for s in idx]
        vals = [v for v in vals if not pd.isna(v)]
        if not vals:
            continue
        a = np.array(vals)
        big, mid, small = p.grade_cuts
        out[w] = {'n': int(a.size), 'median': float(np.median(a)),
                  'p_small': float((a <= small).mean()),
                  'p_mid': float((a <= mid).mean()),
                  'p_big': float((a <= big).mean())}
    return out


def signal_day_levels(df: pd.DataFrame, cl: pd.DataFrame, p: Params) -> list[dict]:
    """
    给走势图用的逐日信号标注 —— 不是集群统计, 是可视化用的"这一天单独往后
    看 grade_window 天回撤多少, 归到哪个等级"。
    """
    close = df['index_close']
    sig_idx = np.flatnonzero(df['signal'].values)
    ranges = []
    if not cl.empty:
        valid = cl[cl.type == '有效集群']
        pos = {d: i for i, d in enumerate(df.index)}
        ranges = [(pos[r.start_date], pos[r.end_date], int(r.cluster_id))
                  for r in valid.itertuples() if r.start_date in pos and r.end_date in pos]

    def cluster_of(i: int):
        for lo, hi, cid in ranges:
            if lo <= i <= hi:
                return cid
        return None

    out = []
    for i in sig_idx:
        dd = forward_drawdown(close, i, p.grade_window, p.dd_mode)
        out.append({'date': str(df.index[i].date()), 'index': int(i),
                    'level': grade(dd, p.grade_cuts), 'cluster_id': cluster_of(i)})
    return out


def current_status(df: pd.DataFrame, cl: pd.DataFrame, p: Params) -> dict:
    """
    "此刻"的状态快照

    recent_signal_count: 不要求这些信号属于同一个集群, 单纯统计原始频率。
    active_cluster: 如果最近一个集群的 end_date 落在这个窗口内, 视为"仍新鲜",
                    附带它的确认状态(是单点还是已升级为有效集群)。
    """
    n = len(df)
    out = {'as_of': str(df.index[-1].date()) if n else None,
          'window': p.cluster_window,
          'last_signal_date': None, 'recent_signal_count': 0,
          'active_cluster': None}
    if not n:
        return out

    sig_idx = np.flatnonzero(df['signal'].values)
    if len(sig_idx):
        out['last_signal_date'] = str(df.index[sig_idx[-1]].date())
        cutoff = n - 1 - p.cluster_window
        out['recent_signal_count'] = int((sig_idx > cutoff).sum())

    if not cl.empty:
        window_start = df.index[max(0, n - 1 - p.cluster_window)]
        fresh = cl[cl['end_date'] >= window_start]
        if len(fresh):
            r = fresh.sort_values('end_date').iloc[-1]
            out['active_cluster'] = {
                'id': int(r.cluster_id), 'type': r.type,
                'start': str(r.start_date.date()), 'end': str(r.end_date.date()),
                'count': int(r.signal_count),
                'confirm_date': (None if pd.isna(r.confirm_date)
                                 else str(r.confirm_date.date())),
            }
    return out


def summarize(df: pd.DataFrame, cl: pd.DataFrame, p: Params) -> dict:
    """
    研报口径的命中率 + 两组无条件对照。
    """
    valid = cl[cl.type == '有效集群'] if not cl.empty else cl
    gradable = valid[valid.level != '近期信号'] if not valid.empty else valid
    col = f'dd_{p.grade_window}d'
    big, mid = p.grade_cuts[0], p.grade_cuts[1]
    hit_mid = int((gradable[col] <= mid).sum()) if len(gradable) else 0
    hit_big = int((gradable[col] <= big).sum()) if len(gradable) else 0
    hit_rate = hit_mid / len(gradable) if len(gradable) else None
    hit_rate_big = hit_big / len(gradable) if len(gradable) else None

    base_all = baseline_rates(df, p)
    base_up = baseline_rates(df, p, mask=df['cond1'])
    gw = p.grade_window

    def excess_of(rate, key):
        if rate is None or gw not in base_all:
            return None
        return {'vs_all': rate - base_all[gw][key],
                'vs_uptrend': (rate - base_up[gw][key] if gw in base_up else None)}

    excess = excess_of(hit_rate, 'p_mid')
    excess_big = excess_of(hit_rate_big, 'p_big')

    # 每个窗口(周/月/季/年)各自的命中率与超额 —— 而不是只看 grade_window。
    # 用该窗口自己 notna 的集群数做分母, 不是套用 252 日口径的 gradable:
    # 一个刚触发的近期集群可能 5 日回撤已经有数, 但 252 日还没观察满。
    window_hits = {}
    for w in p.dd_windows:
        cw = f'dd_{w}d'
        sub = valid[valid[cw].notna()] if not valid.empty else valid
        nw = len(sub)
        if nw == 0:
            continue
        hm = int((sub[cw] <= mid).sum())
        hb = int((sub[cw] <= big).sum())
        rm, rb = hm / nw, hb / nw
        ba = base_all.get(w)
        window_hits[w] = {
            'n': nw, 'hit_mid': hm, 'hit_big': hb,
            'rate_mid': rm, 'rate_big': rb,
            'excess_mid': (rm - ba['p_mid']) if ba else None,
            'excess_big': (rb - ba['p_big']) if ba else None,
        }

    cluster_box = {}
    for w in p.dd_windows:
        ss = gradable[f'dd_{w}d'].dropna() if len(gradable) else pd.Series(dtype=float)
        if len(ss):
            cluster_box[w] = {'n': int(len(ss)), 'median': float(ss.median()),
                              'q1': float(ss.quantile(0.25)),
                              'q3': float(ss.quantile(0.75)),
                              'min': float(ss.min()), 'max': float(ss.max()),
                              'worst': float(ss.min()), 'best': float(ss.max())}

    status = current_status(df, cl, p)

    return dict(
        n_days=int(len(df)), signal_days=int(df.signal.sum()),
        cond_rate={f'cond{i}': float(df[f'cond{i}'].mean()) for i in range(1, 5)},
        n_clusters_total=int(len(cl)), n_clusters_valid=int(len(valid)),
        n_gradable=int(len(gradable)),
        hit_mid=hit_mid, hit_big=hit_big, hit_rate=hit_rate, hit_rate_big=hit_rate_big,
        level_counts=(valid['level'].value_counts().to_dict() if len(valid) else {}),
        cluster_box=cluster_box, window_hits=window_hits,
        baseline_all=base_all, baseline_uptrend=base_up,
        excess=excess, excess_big=excess_big, status=status,
    )


# =====================================================================
# 编排
# =====================================================================

class Omen:
    def __init__(self, strat: Strategy, panel: Panel, store: IndexStore,
                 cache: Optional[BreadthCache] = None):
        self.s, self.panel, self.store, self.cache = strat, panel, store, cache
        self.stats: Optional[pd.DataFrame] = None
        self.index_full: Optional[pd.Series] = None   # 带预热段的指数, 供条件①用
        self.index_source = strat.benchmark

    def compute(self, start: str, end: str) -> pd.DataFrame:
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

        # 各策略的实际起点因此由"配置的 start" 和 "指数真实起点" 中较晚的那个决定。
        first = df['index_close'].first_valid_index()
        if first is None:
            raise ValueError(f'{s.benchmark} 在 {start}~{end} 内没有任何指数数据')
        if first > df.index[0]:
            df = df.loc[first:]

        df = build_signals(df, p, index_full=self.index_full)
        self.stats = df
        return df

    def run(self) -> pd.DataFrame:
        cl = build_clusters(self.stats, self.s.params)
        if not cl.empty:
            nv = (cl.type == '有效集群').sum()
            print(f'[{self.s.name}] {len(cl)} 个事件 ({nv} 有效集群 / {len(cl)-nv} 单点)')
        else:
            print(f'[{self.s.name}] 无信号')
        return evaluate(self.stats, cl, self.s.params)

    def report(self, cl: pd.DataFrame):
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


def _end_date(panel: Panel) -> str:
    """
    回测终点。
    """
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
        pass          # 日历核对只是提醒, 失败不该影响回测
    return local_end.strftime('%Y-%m-%d')


def _make_store(allow_remote: bool = True) -> IndexStore:
    return IndexStore(source=jq_source.make_source(allow_remote),
                      allow_remote=allow_remote)


def _prepare(key: str, store=None, cache=None):
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
    """回测起点之前的指数收盘, 给 backtest.js 算条件① 的均线用。"""
    full = omen.index_full
    if full is None:
        return {'dates': [], 'index_close': []}
    pre = full[full.index < df.index[0]].tail(config.INDEX_PREHEAT)
    return {'dates': [str(d.date()) for d in pre.index],
            'index_close': [None if pd.isna(v) else round(float(v), 3) for v in pre]}


def export_payload(omen: Omen, cl: pd.DataFrame) -> dict:
    """
    导出可视化用的数据。刻意包含 index_close/high_ratio/low_ratio/new_high/
    new_low/mcclellan 这些"中间序列"——参数实验室靠它们在浏览器里重算轻参数,
    没有它们就只能看死结果。
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
        # 只导出 heavy_keys: 参数实验室用它把重参数列成"只读"区。
        'heavy_keys': sorted(config.HEAVY_KEYS),
        'meta': {'start': str(df.index[0].date()), 'end': str(df.index[-1].date()), **m},
        # 逐日信号标注, 供走势图用 —— 每个信号日一个点, 不只是集群
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
    """生成 HTML。默认先跑 export 拿最新数据, --reuse 则直接用已有 JSON。"""
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
    更新各策略基准指数的收盘序列。

    reset_head: 清除指定指数(或 'all')的 head_floor 标记。
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
    """
    价格面板 + 状态表一起更新。
    """
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
    """从聚宽拉指数成分股。本地缺哪个指数的名单就拉哪个。"""
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
    """
    逐日打印四条件的原始值, 诊断"某天为什么没触发/触发了"。
    """
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
    """对比可交易域规则。条件①统一用真实基准指数, 差异只来自可交易域。"""
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
