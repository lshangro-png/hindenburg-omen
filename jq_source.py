"""
聚宽（JoinQuant）数据源，本项目唯一的远端数据来源。

    认证与额度    jq_auth / quota / guard
    代码转换      to_jq_code / from_jq_code（.SH/.SZ/.BJ ↔ .XSHG/.XSHE/.BJSE）
    指数          JQSource（供 IndexStore 调用）
    成分股        fetch_constituents
    面板更新      update_price_panel / update_state_table

额度保护（参数见 config.py 的 JQ_*）：
    · 拉取前先查剩余额度，扣完会低于 JQ_RESERVE 就拒绝
    · 单次运行累计行数有上限，防止代码出错把额度刷光
    · JQ_DRY_RUN=1 只打印计划，不真拉
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pandas as pd

import config

_jq = None            # 已认证的 jqdatasdk 模块，保证只认证一次
_used_rows = 0        # 本次运行累计消耗的行数（估算）


# =====================================================================
# 认证与额度
# =====================================================================

def jq_auth(user: Optional[str] = None, password: Optional[str] = None):
    """认证聚宽，只做一次（重复认证会挤掉自己的上一个会话）。"""
    global _jq
    if _jq is not None:
        try:
            if _jq.is_auth():
                return _jq
        except Exception:
            pass

    import jqdatasdk as jq

    try:
        authed = jq.is_auth()
    except Exception:
        authed = False

    if not authed:
        user = user or os.getenv('JQ_USER')
        password = password or os.getenv('JQ_PASSWORD')
        if not user or not password:
            raise RuntimeError('缺少聚宽账号。请设置 JQ_USER / JQ_PASSWORD')
        jq.auth(str(user), str(password))

    _jq = jq
    print(f'[JQ] 认证成功。{quota_str()}')
    return _jq


def quota() -> dict:
    jq = _jq if _jq is not None else jq_auth()
    try:
        return dict(jq.get_query_count())
    except Exception as e:
        return {'total': None, 'spare': None, 'error': str(e)}


def quota_str() -> str:
    q = quota()
    if q.get('spare') is None:
        return f'额度查询失败 ({q.get("error")})'
    return (f'今日额度剩余 {q["spare"]:,} / {q["total"]:,} '
            f'({q["spare"] / q["total"]:.0%})')


def est_rows(start, end, n_codes: int = 1) -> int:
    """按每年 245 个交易日估算行数。"""
    days = (pd.Timestamp(end) - pd.Timestamp(start)).days
    return max(1, int(days / 365 * 245) + 5) * n_codes


def guard(need: int, cap: Optional[int] = None):
    """拉取前检查：本次累计不超过 cap，且扣完后剩余额度不低于 JQ_RESERVE。"""
    cap = cap if cap is not None else config.JQ_MAX_ROWS

    if _used_rows + need > cap:
        raise RuntimeError(
            f'本次运行已估算消耗 {_used_rows:,} 行, 再拉 {need:,} 行会超过上限 '
            f'{cap:,}。确认无误可调高对应的 JQ_MAX_ROWS* 环境变量。')

    q = quota()
    spare = q.get('spare')
    if spare is None:
        print('[JQ] ⚠ 无法查询额度, 按谨慎处理: 仍继续但请留意用量')
        return
    if spare - need < config.JQ_RESERVE:
        raise RuntimeError(
            f'剩余额度 {spare:,} 行, 本次需约 {need:,} 行, 扣完将低于给他人预留的 '
            f'{config.JQ_RESERVE:,} 行下限, 已拒绝。今日请用 HO_OFFLINE=1 跑纯本地, '
            f'或明日额度重置后再更新。')
    print(f'[JQ] 预估消耗 {need:,} 行, 剩余 {spare:,} 行, 通过额度检查')


# =====================================================================
# 代码转换
# =====================================================================

_ALIAS = {
    '000001.SH': '000001.XSHG',    # 上证综指
    '000985.CSI': '000985.XSHG',   # 中证全指
    '000300.SH': '000300.XSHG',    # 沪深300
    '000905.SH': '000905.XSHG',    # 中证500
    '000852.SH': '000852.XSHG',    # 中证1000
    '000688.SH': '000688.XSHG',    # 科创50
    '399006.SZ': '399006.XSHE',    # 创业板指
    '399001.SZ': '399001.XSHE',    # 深证成指
    '399673.SZ': '399673.XSHE',    # 创业板50
}


def to_jq_code(code: str) -> str:
    """
    面板代码 → 聚宽代码，例如 000985.CSI → 000985.XSHG，830799.BJ → 830799.BJSE。
    没有后缀时按代码段判断交易所。
    """
    if code in _ALIAS:
        return _ALIAS[code]
    if code.endswith(('.XSHG', '.XSHE', '.BJSE')):
        return code
    num, _, suf = code.partition('.')
    suf = suf.upper()
    if suf in ('SH', 'SS'):
        return f'{num}.XSHG'
    if suf in ('SZ', 'SE'):
        return f'{num}.XSHE'
    if suf in ('BJ', 'BSE', 'BJSE', 'NQ'):
        return f'{num}.BJSE'
    # 无后缀：8/4/920 开头为北交所，399/395 为深交所，其余为上交所
    if num.startswith(('8', '4', '920')):
        return f'{num}.BJSE'
    return f'{num}.XSHE' if num.startswith(('399', '395')) else f'{num}.XSHG'


# =====================================================================
# 指数源 (供 IndexStore 调用)
# =====================================================================

class JQSource:
    """聚宽指数收盘价源，只负责 fetch；何时调用由 IndexStore 决定。"""

    name = 'jq'

    def __init__(self, field: str = 'close'):
        self.field = field

    def _get(self, jq_code: str, start: str, end: str) -> pd.DataFrame:
        global _used_rows
        jq = jq_auth()
        if config.JQ_DRY_RUN:
            print(f'[JQ] DRY RUN: 本应拉取 {jq_code} {start} ~ {end}')
            return pd.DataFrame()
        df = jq.get_price(jq_code, start_date=str(start), end_date=str(end),
                          frequency='daily', fields=[self.field],
                          skip_paused=False, fq=None)
        _used_rows += len(df) if df is not None else 0
        return df

    def fetch(self, code: str, start: str, end: str) -> pd.Series:
        """拉取 [start, end] 的收盘价；没有数据时返回空序列而不是报错。"""
        jq_code = to_jq_code(code)
        guard(est_rows(start, end))

        df = self._get(jq_code, start, end)

        # 交易所猜错时换一个再试一次
        if (df is None or df.empty) and jq_code.endswith(('.XSHG', '.XSHE')):
            alt = (jq_code.replace('.XSHG', '.XSHE') if jq_code.endswith('.XSHG')
                   else jq_code.replace('.XSHE', '.XSHG'))
            df2 = self._get(alt, start, end)
            if df2 is not None and not df2.empty:
                print(f'[JQ] {code}: {jq_code} 无数据, 改用 {alt}')
                jq_code, df = alt, df2

        empty = pd.Series(dtype='float64', name=code, index=pd.DatetimeIndex([]))
        if df is None or df.empty:
            # 增量补最近几天时为空是正常的
            days_back = (pd.Timestamp.today().normalize() - pd.Timestamp(start)).days
            if days_back <= 10 or config.JQ_DRY_RUN:
                print(f'[JQ] {code} 区间 {start}~{end} 暂无新数据')
            else:
                print(f'[JQ] ⚠ {code} -> {jq_code} 在 {start}~{end} 返回空 '
                      f'(代码不存在或账号无该年限权限)')
            return empty

        s = pd.Series(df[self.field].values,
                      index=pd.to_datetime(df.index), name=code).dropna().sort_index()
        s = s[~s.index.duplicated(keep='last')]
        if s.empty:
            # 早于指数起点的区间，聚宽返回的是「日期齐全但值全 NaN」的表
            print(f'[JQ] ⚠ {code} 在 {start}~{end} 只有空值行, 判定为无数据'
                  f'(该指数在这段时间可能尚未开始编制)')
            return empty
        print(f'[JQ] {code} 取到 {len(s)} 行 '
              f'({s.index.min():%Y-%m-%d} ~ {s.index.max():%Y-%m-%d}), '
              f'本次运行累计 {_used_rows:,} 行')
        return s

    @staticmethod
    def search(keyword: str) -> pd.DataFrame:
        """
        按名称模糊查找指数代码，例如 JQSource.search('中证全指')。
        主流程不调用，供新增策略时在交互环境里手动查代码。
        """
        jq = jq_auth()
        idx = jq.get_all_securities(types=['index'])
        return idx[idx['display_name'].str.contains(keyword, na=False)]


def from_jq_code(code: str) -> str:
    """聚宽代码 → 面板代码（to_jq_code 的逆运算）。"""
    num, _, suf = str(code).partition('.')
    return {'XSHG': f'{num}.SH', 'XSHE': f'{num}.SZ',
            'BJSE': f'{num}.BJ'}.get(suf.upper(), str(code))


def fetch_constituents(index_code: str, start: str, end: str,
                       out_dir: str = None, freq: str = 'ME') -> Path:
    """
    拉取指数成分股并保存为 index_constituents_<代码>.pkl。

    按月末采样（成分股半年调一次，月频足够），
    输出 MultiIndex(date, code)、含 weight 列，代码统一为面板格式。
    """
    jq = jq_auth()
    jq_code = to_jq_code(index_code)
    dates = pd.date_range(start, end, freq=freq)
    if len(dates) == 0:
        raise ValueError(f'{start}~{end} 区间内没有采样点')

    frames, missed = [], 0
    for d in dates:
        try:
            w = jq.get_index_weights(jq_code, date=str(d.date()))
        except Exception:
            missed += 1
            continue
        if w is None or len(w) == 0:
            missed += 1
            continue
        frames.append(pd.DataFrame({'weight': w['weight'].values},
                                   index=pd.MultiIndex.from_product(
                                       [[d], list(w.index)], names=['date', 'code'])))
    if not frames:
        raise ValueError(f'{index_code} 没取到任何成分股, 检查代码或账号权限')

    out = pd.concat(frames).sort_index()
    # 统一成面板的代码格式，否则和价格面板的列对不上
    codes = out.index.get_level_values('code').map(from_jq_code)
    out.index = pd.MultiIndex.from_arrays(
        [out.index.get_level_values('date'), codes], names=['date', 'code'])

    d = Path(out_dir or config.DATA_DIR)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f'index_constituents_{index_code}.pkl'
    out.to_pickle(p)
    n = out.groupby(level='date').size().mean()
    print(f'{index_code}: {len(frames)} 个采样日, 日均 {n:.0f} 只成分股'
          + (f' ({missed} 个日期无数据)' if missed else ''))
    print(f'已保存 {p}')
    return p


def _isolate_bad_codes(jq, jq_codes: list[str], probe_date: str,
                       fields: list[str], batch_size: int = None
                       ) -> tuple[list[str], list[tuple[str, str]]]:
    """
    找出聚宽不认识的代码（改名、退市多年等），返回 (可用代码, [(坏代码, 报错)])。

    聚宽批量请求里只要有一个无效代码就整批报错，所以用二分法隔离。
    只在一个探测日期上查一次即可。
    """
    batch_size = batch_size or BATCH_SIZE
    bad = []

    def check(codes):
        if not codes:
            return
        try:
            jq.get_price(codes, start_date=probe_date, end_date=probe_date,
                        frequency='daily', fields=fields, skip_paused=False,
                        fq=None, panel=False)
        except Exception as e:
            if len(codes) == 1:
                bad.append((codes[0], str(e).strip()[:60]))
                return
            mid = len(codes) // 2
            check(codes[:mid])
            check(codes[mid:])

    for i in range(0, len(jq_codes), batch_size):
        check(jq_codes[i:i + batch_size])

    bad_set = {c for c, _ in bad}
    good = [c for c in jq_codes if c not in bad_set]
    return good, bad


def update_state_table(state_path: Path, end: Optional[str] = None,
                       dry_run: bool = False) -> None:
    """
    把 stock_state.pkl 增量更新到最新交易日（旧文件备份为 .pkl.bak）。

    新增行的字段与原文件一致：
        trade_status  '交易' / '停牌一天'（原数据的中文取值）
        status_t      1 = 当日可交易，0 = 不可
        maxupdown     1 = 涨停，-1 = 跌停，0 = 否
        isst / issts  ST / *ST（聚宽只给 is_st，两列填同一个值）
        issusp        暂停上市（固定填 0）
        lenlist       上市自然日数
    """
    old = pd.read_pickle(state_path)
    last_date = pd.to_datetime(old.index.get_level_values('trade_dt')).max()

    jq = jq_auth()
    end_ts = pd.Timestamp(end) if end else _last_closed_trade_day(jq)
    print(f'状态表最后一天 {last_date:%Y-%m-%d} → 目标 {end_ts:%Y-%m-%d}')
    if end_ts <= last_date:
        print('已是最新, 无需更新')
        return

    days = [pd.Timestamp(d) for d in
            jq.get_trade_days(start_date=str((last_date + pd.Timedelta(days=1)).date()),
                              end_date=str(end_ts.date()))]
    if not days:
        print('区间内没有新交易日')
        return
    print(f'需补 {len(days)} 个交易日: {days[0]:%Y-%m-%d} ~ {days[-1]:%Y-%m-%d}')
    if dry_run:
        return

    codes = sorted({str(c) for c in old.index.get_level_values('stcode').unique()})
    jq_codes = [to_jq_code(c) for c in codes]
    back = {j: c for j, c in zip(jq_codes, codes)}

    fields = ['close', 'paused', 'high_limit', 'low_limit']
    # 先剔除聚宽不认识的代码，否则整批请求会失败
    good_codes, bad_codes = _isolate_bad_codes(jq, jq_codes, str(days[-1].date()), fields)
    if bad_codes:
        print(f'{len(bad_codes)} 只代码聚宽认不出, 已从本次更新中剔除:')
        for c, msg in bad_codes[:10]:
            print(f'  {back.get(c, c)}({c}): {msg}')
        if len(bad_codes) > 10:
            print(f'  ... 还有 {len(bad_codes)-10} 只')

    info = jq.get_all_securities(types=['stock'], date=str(end_ts.date()))
    start_dates = {from_jq_code(i): pd.Timestamp(r['start_date'])
                   for i, r in info.iterrows()}

    frames = []
    for d in days:
        ds = str(d.date())
        parts = []
        for i in range(0, len(good_codes), BATCH_SIZE):
            batch = good_codes[i:i + BATCH_SIZE]
            try:
                px = jq.get_price(batch, start_date=ds, end_date=ds, frequency='daily',
                                  fields=fields, skip_paused=False, fq=None, panel=False)
            except Exception as e:
                print(f'  ⚠ {ds} 批次取失败, 跳过这批({len(batch)}只): {e}')
                continue
            if px is not None and len(px):
                parts.append(px)
        if not parts:
            continue
        px = pd.concat(parts).set_index('code')
        try:
            st = jq.get_extras('is_st', good_codes, start_date=ds, end_date=ds, df=True)
            st_row = st.iloc[0] if len(st) else pd.Series(dtype=bool)
        except Exception:
            st_row = pd.Series(dtype=bool)

        rows = {}
        for j in px.index:
            code = back.get(j, from_jq_code(j))
            c, paused = px.at[j, 'close'], px.at[j, 'paused']
            hl, ll = px.at[j, 'high_limit'], px.at[j, 'low_limit']
            is_st = bool(st_row.get(j, False))
            tradable = int((not bool(paused)) and pd.notna(c))
            mud = 0.0
            if pd.notna(c):
                if pd.notna(hl) and c >= hl:
                    mud = 1.0
                elif pd.notna(ll) and c <= ll:
                    mud = -1.0
            sd = start_dates.get(code)
            rows[code] = {
                'trade_status': '交易' if tradable else '停牌一天',
                'status_t': tradable,
                'maxupdown': mud,
                'isst': int(is_st),
                'issts': int(is_st),
                'issusp': 0,
                'lenlist': int((d - sd).days) if sd is not None else 0,
            }
        if rows:
            frames.append(pd.DataFrame.from_dict(rows, orient='index').rename_axis('stcode')
                          .assign(trade_dt=d).set_index('trade_dt', append=True)
                          .reorder_levels(['trade_dt', 'stcode']))

    if not frames:
        print('没有取到任何新数据')
        return

    new = pd.concat(frames)
    new = new.reindex(columns=old.columns)         # 列与旧文件保持一致
    out = pd.concat([old, new])
    out = out[~out.index.duplicated(keep='last')].sort_index()

    bak = state_path.with_suffix('.pkl.bak')
    state_path.replace(bak)
    try:
        out.to_pickle(state_path)
    except Exception:
        bak.replace(state_path)
        raise
    added = len(out) - len(old)
    print(f'状态表 {len(old):,} → {len(out):,} 行 (+{added:,}), '
          f'覆盖至 {pd.to_datetime(out.index.get_level_values("trade_dt")).max():%Y-%m-%d}')
    print(f'旧文件已备份为 {bak.name}')


def make_source(allow_remote: bool = True):
    """返回聚宽指数源；离线、未安装 jqdatasdk 或未配置账号时返回 None（纯本地）。"""
    if config.OFFLINE or not allow_remote:
        print('[Store] 纯本地模式, 不联网')
        return None
    import importlib.util
    if importlib.util.find_spec('jqdatasdk') is None:
        print('[Store] 未安装 jqdatasdk, 按纯本地模式运行')
        return None
    if not os.getenv('JQ_USER'):
        print('[Store] 未配置 JQ_USER, 按纯本地模式运行')
        return None
    print('[Store] 远端数据源: 聚宽')
    return JQSource()


# =====================================================================
# 股票价格面板增量更新
# =====================================================================

BATCH_SIZE = 2500  # 单次 get_price 请求的股票数上限


def _fetch_close_wide(jq, jq_codes: list[str], start: str, end: str, fq) -> pd.DataFrame:
    df = jq.get_price(jq_codes, start_date=start, end_date=end, frequency='daily',
                      fields=['close'], fq=fq, panel=False, skip_paused=False)
    if df is None or df.empty:
        return pd.DataFrame()
    return df.pivot(index='time', columns='code', values='close').sort_index()


def _fetch_in_batches(jq, jq_codes: list[str], start: str, end: str, fq,
                      batch_size: int = BATCH_SIZE) -> list[pd.DataFrame]:
    """分批拉取收盘价；某批失败时二分重试，只跳过真正出错的代码。"""
    parts, bad = [], []

    def pull(codes, depth=0):
        if not codes:
            return
        try:
            w = _fetch_close_wide(jq, codes, start, end, fq)
            if not w.empty:
                parts.append(w)
        except Exception as e:
            if len(codes) == 1:
                bad.append((codes[0], str(e).strip()[:60]))
                return
            mid = len(codes) // 2
            if depth == 0:
                print(f'    ⚠ 该批出错, 二分隔离中: {str(e).strip()[:60]}')
            pull(codes[:mid], depth + 1)
            pull(codes[mid:], depth + 1)

    batches = [jq_codes[i:i + batch_size] for i in range(0, len(jq_codes), batch_size)]
    print(f'[Panel] 分 {len(batches)} 批拉取 (每批 ≤{batch_size} 只)')
    for i, b in enumerate(batches, 1):
        print(f'  批次 {i}/{len(batches)} ({len(b)} 只)...')
        pull(b)

    if bad:
        print(f'[Panel] {len(bad)} 只代码拉取失败, 已跳过 (保持 NaN):')
        for c, msg in bad[:10]:
            print(f'    {c}: {msg}')
        if len(bad) > 10:
            print(f'    ... 另有 {len(bad) - 10} 只')
    return parts


def _classify(securities: pd.DataFrame, jq_codes: list[str],
              last_date: pd.Timestamp) -> dict[str, str]:
    """
    按聚宽的上市 / 退市日期给股票分类（只看价格是否为 NaN 分不清未上市和已退市）。

    返回 {jq_code: 'normal' | 'new_listing' | 'delisted' | 'unknown'}
    """
    info = securities
    info.index = [str(i) for i in info.index]
    out = {}
    for c in jq_codes:
        if c not in info.index:
            out[c] = 'unknown'
            continue
        row = info.loc[c]
        start_date = pd.Timestamp(row['start_date'])
        end_date = row.get('end_date')
        end_date = pd.Timestamp(end_date) if pd.notna(end_date) else None
        if end_date is not None and end_date <= last_date:
            out[c] = 'delisted'
        elif start_date > last_date:
            out[c] = 'new_listing'
        else:
            out[c] = 'normal'
    return out


def latest_trade_day() -> pd.Timestamp:
    """最近一个已收盘的交易日（按聚宽交易日历；当天 15:30 前算前一个交易日）。"""
    jq = jq_auth()
    return _last_closed_trade_day(jq)


def _last_closed_trade_day(jq) -> pd.Timestamp:
    today = pd.Timestamp.today().normalize()
    try:
        days = jq.get_trade_days(end_date=str(today.date()), count=10)
    except Exception:
        return today          # 取不到日历就用今天
    if len(days) == 0:
        return today
    days = [pd.Timestamp(d) for d in days]
    last = days[-1]
    now = pd.Timestamp.now()
    if last == today and now.hour * 60 + now.minute < 15 * 60 + 30:
        last = days[-2] if len(days) >= 2 else last
    return last


def update_price_panel(panel_path: Path, end: Optional[str] = None,
                       dry_run: bool = False, local_table=None):
    """
    把 stock_price_adj_close.pkl 增量更新到最新交易日（旧文件备份为 .pkl.bak）。

    拼接方法：拉一段后复权价，只取其中的日涨跌幅，再从旧文件最后一天的价格
    往后复利滚算。这样即使期间有分红送股（复权基准变了），接缝处也不会跳变。

    注意：
      · 不能用不复权价算涨跌幅，除权日的断崖下跌会被当成真实下跌，伪造出新低。
      · 只更新面板里已有的股票，之后新上市的股票不会自动加列。
    """
    px = pd.read_pickle(panel_path)
    px.index = pd.to_datetime(px.index)
    last_date = px.index[-1]

    jq = jq_auth()
    end_ts = pd.Timestamp(end) if end else _last_closed_trade_day(jq)

    print(f'本地面板 {px.shape[0]} 天 x {px.shape[1]} 只, 最后一天 {last_date:%Y-%m-%d}'
          f' → 目标 {end_ts:%Y-%m-%d}')
    if end_ts <= last_date:
        print('已是最新, 无需更新')
        return

    fetch_start, fetch_end = last_date.strftime('%Y-%m-%d'), end_ts.strftime('%Y-%m-%d')

    codes = list(px.columns)
    code_map = {c: to_jq_code(c) for c in codes}
    jq_codes = list(code_map.values())
    inv_map = {v: k for k, v in code_map.items()}

    # 先按上市 / 退市日期分类，只请求正常交易的股票（退市代码会让整批请求失败）
    print('[Panel] 按真实上市/退市日期分类 (区分"新股"/"退市股"/"无效代码")...')
    loader = lambda: jq.get_all_securities(types=['stock'])  # noqa: E731
    securities = local_table.get('securities', loader) if local_table else loader()
    cls = _classify(securities, jq_codes, last_date)
    cls_local = {inv_map.get(k, k): v for k, v in cls.items()}
    n = {k: sum(1 for v in cls_local.values() if v == k)
         for k in ('normal', 'new_listing', 'delisted', 'unknown')}
    print(f"[Panel] 正常 {n['normal']} 只, 新上市 {n['new_listing']} 只, "
          f"已退市 {n['delisted']} 只, 聚宽无此代码 {n['unknown']} 只")
    if n['delisted'] or n['unknown']:
        skip_eg = [c for c, v in cls_local.items() if v in ('delisted', 'unknown')][:5]
        print(f"[Panel] 跳过 {n['delisted'] + n['unknown']} 只不该请求的代码, 例: {skip_eg}")

    fetch_codes = [code_map[c] for c, v in cls_local.items()
                   if v == 'normal' and c in px.columns]
    if not fetch_codes:
        print('[Panel] 没有需要更新的正常股票, 中止')
        return

    n_days_est = max(1, (end_ts - last_date).days * 5 // 7 + 3)
    need = len(fetch_codes) * n_days_est
    print(f'[Panel] 待请求 {len(fetch_codes)} 只 (总列数 {len(jq_codes)}), '
          f'预估交易日 {n_days_est}, 预估行数 {need:,}')

    if dry_run:
        print('[Panel] --dry-run: 到此为止, 不实际拉取')
        return

    guard(need, cap=config.JQ_MAX_ROWS_PANEL)

    parts = _fetch_in_batches(jq, fetch_codes, fetch_start, fetch_end, fq='post')
    if not parts:
        print('[Panel] 未取到任何数据, 中止')
        return

    raw = pd.concat(parts, axis=1)
    raw.columns = [inv_map.get(c, c) for c in raw.columns]
    raw.index = pd.to_datetime(raw.index)
    raw = raw.sort_index()

    if last_date not in raw.index:
        raise RuntimeError(f'新拉数据里没有锚点日 {last_date:%Y-%m-%d}, 无法安全拼接; '
                           f'请检查交易日历或用 --end 指定更早的日期重试')

    anchor = px.loc[last_date]
    new_dates = raw.index[raw.index > last_date]
    if len(new_dates) == 0:
        print('[Panel] 没有新增交易日')
        return

    pct = raw.pct_change(fill_method=None)
    block = pd.DataFrame(index=new_dates, columns=px.columns, dtype='float64')
    for code in px.columns:
        status = cls_local.get(code, 'unknown')
        if status in ('delisted', 'new_listing') or code not in pct.columns:
            continue          # 退市股保持 NaN
        base = anchor.get(code)
        if pd.isna(base):
            continue
        block[code] = base * (1 + pct.loc[new_dates, code].fillna(0)).cumprod()

    new_codes = [c for c, v in cls_local.items() if v == 'new_listing' and c in px.columns]
    if new_codes:
        print(f'[Panel] {len(new_codes)} 只确认为新上市, 用后复权价直接补')
        np_parts = _fetch_in_batches(jq, [code_map[c] for c in new_codes],
                                     fetch_start, fetch_end, fq='post')
        if np_parts:
            w = pd.concat(np_parts, axis=1)
            w.columns = [inv_map.get(c, c) for c in w.columns]
            w.index = pd.to_datetime(w.index)
            for c in new_codes:
                if c in w.columns:
                    block[c] = w[c].reindex(new_dates)

    updated = pd.concat([px, block.astype('float64')])
    updated = updated[~updated.index.duplicated(keep='last')].sort_index()
    print(f'[Panel] 新增 {len(new_dates)} 个交易日 '
          f'({new_dates[0]:%Y-%m-%d} ~ {new_dates[-1]:%Y-%m-%d})')
    print(f'[Panel] 更新后: {updated.shape[0]} 天 x {updated.shape[1]} 只')

    backup = panel_path.with_suffix('.pkl.bak')
    px.to_pickle(backup)
    print(f'[Panel] 已备份旧文件 -> {backup.name}')
    updated.to_pickle(panel_path)
    print(f'[Panel] 已写入 {panel_path}')
    print(f'[Panel] {quota_str()}')
