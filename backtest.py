"""
回测核心：纯函数，不读写文件、不联网。

流程：
    中间序列 ──build_signals──▶ 四条件 + 信号
             ──build_clusters─▶ 集群
             ──evaluate───────▶ 每个集群的回撤与等级
             ──summarize──────▶ 命中率、基准率、当前状态

中间序列是按交易日索引的 DataFrame，需含列：
    index_close, high_ratio, low_ratio, new_high, new_low, mcclellan

assets/backtest.js 是这些函数的 JS 镜像（供参数实验室在浏览器里重算），
改这里的逻辑时要同步改那边。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from config import Params


# =====================================================================
# 1. 信号
# =====================================================================

def build_signals(df: pd.DataFrame, p: Params,
                  index_full: Optional[pd.Series] = None) -> pd.DataFrame:
    """
    计算四个条件和信号列（cond1..cond4, signal）。

    index_full: 带预热段的指数收盘价，用于条件①的均线，
                让回测首日的均线也有值。不传则用 df['index_close']。
    """
    src = df['index_close'] if index_full is None else index_full
    ma = src.rolling(p.ma_period).mean()
    if p.trend_mode == 'ma_rising':
        # 均线在上升：MA(t) > MA(t - lookback)，美股原始定义
        cond1 = ma > ma.shift(p.ma_lookback)
    elif p.trend_mode == 'above_ma':
        # 价格在均线上方：Close(t) > MA(t)，浙商 A 股适配版
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


# =====================================================================
# 2. 集群
# =====================================================================

def build_clusters(df: pd.DataFrame, p: Params) -> pd.DataFrame:
    """
    把信号日聚成集群，信号数 ≥ cluster_min 的为「有效集群」，其余为「单点」。

    cluster_mode:
        fixed  窗口从集群首日起固定 cluster_window 个交易日（默认）
        chain  每来一个新信号就把窗口往后延，集群可以无限串长
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
         # 确认日 = 第 cluster_min 个信号那天，实盘中最早能知道「这是有效集群」的时点
         'confirm_date': g[p.cluster_min - 1] if len(g) >= p.cluster_min else pd.NaT,
         'signal_count': len(g),
         'type': '有效集群' if len(g) >= p.cluster_min else '单点'}
        for i, g in enumerate(out)])


# =====================================================================
# 3. 回撤与定级
# =====================================================================

def grade(dd, cuts) -> str:
    """按回撤定级；回撤为 NaN（观察期未满）时返回「近期信号」。"""
    if dd is None or pd.isna(dd):
        return '近期信号'
    big, mid, small = cuts
    return ('较大回撤' if dd <= big else '中型回调' if dd <= mid
            else '小幅回调' if dd <= small else '无显著回撤')


def forward_drawdown(close: pd.Series, s: int, w: int, mode: str) -> float:
    """
    从位置 s 起、未来 w 个交易日（含当天）的回撤；数据不够 w 天时返回 NaN。

    mode:
        peak_trough  窗口内峰谷最大回撤（默认）
        from_signal  相对起点收盘价的最大跌幅

    集群评估、基准率、逐日标注都调用这一个函数，保证三处口径一致。
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
    给每个集群加上锚点日、各窗口回撤 dd_{w}d 和等级 level。

    dd_anchor（回撤从哪天起算）:
        confirm  集群确认日（默认，无前视偏差）
        first    集群首日（研报口径；首日当天还不知道会不会成为有效集群，有前视）
    单点没有确认日，一律从首日起算（单点不参与统计）。
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
    基准率：把每一天都当作信号日，统计同口径回撤达到各等级的概率。

    mask: 只在这些日子里抽样。传 cond1 得到「趋势向上时随便挑一天」的基准，
          用来扣除条件①本身的贡献。

    返回 {窗口: {n, median, p_small, p_mid, p_big}}
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
    走势图用的逐日标注：每个信号日单独往后看 grade_window 天的回撤等级，
    并注明它属于哪个有效集群（不属于则为 None）。
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


# =====================================================================
# 4. 汇总
# =====================================================================

def current_status(df: pd.DataFrame, cl: pd.DataFrame, p: Params) -> dict:
    """
    截至最新交易日的状态快照：
        last_signal_date     最近一次信号日
        recent_signal_count  最近 cluster_window 天内的信号次数
        active_cluster       结束日落在这个窗口内的最近一个集群（没有则 None）
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
    汇总统计。主要字段：

        hit_rate / hit_rate_big   有效集群的中型 / 较大回撤命中率（只算观察期已满的）
        baseline_all              全样本基准率
        baseline_uptrend          仅条件①成立日的基准率
        excess / excess_big       命中率 − 基准率
        window_hits               每个回撤窗口各自的命中率与超额
        cluster_box               各窗口回撤分布（画箱线图用）
        status                    current_status() 的结果
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

    # 分母用该窗口自己有数据的集群数：近期集群可能 5 日回撤已有，252 日还没有
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
                              'min': float(ss.min()), 'max': float(ss.max())}

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
