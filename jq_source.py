"""
jq_source.py —— 聚宽 (JoinQuant) 数据源, 本项目唯一的远端来源

账号可能是部门共用的, 所以这个模块的设计目标是"尽量不用":
    1. 每次拉取前先查 get_query_count(), 剩余额度低于 JQ_RESERVE 直接拒绝,
       由调用方兜底用本地数据。
    2. 单次运行累计行数不超过上限, 防止代码写错时把额度刷光。
    3. 拉取前按交易日估算行数并打印, 心里有数再动手。
    4. JQ_DRY_RUN=1 只打印计划、不真拉。

参数见 config.py 的 JQ_* 段。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pandas as pd

import config

_jq = None            # 模块级句柄, 保证只 auth 一次
_used_rows = 0        # 本次运行累计消耗(估算)


# =====================================================================
# 认证与额度
# =====================================================================

def jq_auth(user: Optional[str] = None, password: Optional[str] = None):
    """幂等认证。重复 auth 会挤掉自己上一个会话, 所以只做一次。"""
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
    """按每年 245 个交易日估算行数"""
    days = (pd.Timestamp(end) - pd.Timestamp(start)).days
    return max(1, int(days / 365 * 245) + 5) * n_codes


def guard(need: int, cap: Optional[int] = None):
    """
    拉取前的两道闸: 给别人留的余量 + 自己的上限。
    指数更新和面板更新共用这一套, 只有 cap 不同(面板量级大得多)。
    """
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
    000985.CSI -> 000985.XSHG。聚宽不认 .SH/.SZ/.CSI 后缀。

    北交所必须显式处理成 .BJSE。之前 .BJ 后缀走到最后的兜底分支被拼成
    920981.XSHG —— 一个不存在的代码, 于是 574 只北交所股票在
    update_price_panel 里全被 _classify 判成 'unknown' 静默跳过, 面板里
    永远是 NaN。这种错法没有任何报错, 只表现为"某个板块的数据就是没有"。
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
    # 无后缀时按代码段兜底: 8/4/920 开头是北交所, 399/395 在深交所, 其余上交所
    if num.startswith(('8', '4', '920')):
        return f'{num}.BJSE'
    return f'{num}.XSHE' if num.startswith(('399', '395')) else f'{num}.XSHG'


# =====================================================================
# 指数源 (供 IndexStore 调用)
# =====================================================================

class JQSource:
    """聚宽指数源。只提供 fetch, 由 IndexStore 决定何时调用。"""

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
        jq_code = to_jq_code(code)
        guard(est_rows(start, end))

        df = self._get(jq_code, start, end)

        # 后缀猜错时换一个交易所重试一次
        if (df is None or df.empty) and jq_code.endswith(('.XSHG', '.XSHE')):
            alt = (jq_code.replace('.XSHG', '.XSHE') if jq_code.endswith('.XSHG')
                   else jq_code.replace('.XSHE', '.XSHG'))
            df2 = self._get(alt, start, end)
            if df2 is not None and not df2.empty:
                print(f'[JQ] {code}: {jq_code} 无数据, 改用 {alt}')
                jq_code, df = alt, df2

        empty = pd.Series(dtype='float64', name=code, index=pd.DatetimeIndex([]))
        if df is None or df.empty:
            # 增量补最新几天时返回空是正常的, 不该抛异常打断回测
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
            # get_price 对早于指数真实起点的区间返回的不是空表, 而是"日期
            # 齐全但值全 NaN"的表 —— df.empty 是 False, 但 dropna 之后就
            # 空了。不特判这个的话, s.index.min() 是 NaT, 后面 strftime
            # 直接崩(NaTType does not support strftime)。
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
        按名称模糊查指数代码, 例: JQSource.search('中证全指')

        供在 REPL 里手动查代码用(往 config.STRATEGIES 里加新指数时要先知道
        它的代码), 主流程不调用它 —— 不是死代码, 别顺手删了。
        """
        jq = jq_auth()
        idx = jq.get_all_securities(types=['index'])
        return idx[idx['display_name'].str.contains(keyword, na=False)]


def from_jq_code(code: str) -> str:
    """
    聚宽代码 -> 面板代码。to_jq_code 的逆运算。

    成分股名单是用聚宽代码(.XSHG/.XSHE/.BJSE)返回的, 而价格面板的列名是
    .SH/.SZ/.BJ。不转换的话 reindex 到 px.columns 一个都对不上, 成分股掩码
    全 False, 可交易域变成空 —— 而且不会报错, 只是静默算不出任何信号。
    """
    num, _, suf = str(code).partition('.')
    return {'XSHG': f'{num}.SH', 'XSHE': f'{num}.SZ',
            'BJSE': f'{num}.BJ'}.get(suf.upper(), str(code))


def fetch_constituents(index_code: str, start: str, end: str,
                       out_dir: str = None, freq: str = 'ME') -> Path:
    """
    从聚宽拉指数成分股, 存成 constituents() 认得的格式。

    聚宽的 get_index_weights 一次只能取一个日期的名单, 所以按月末采样再
    前向填充 —— 成分股本来就只在调仓日(半年一次)变动, 月频足够, 而日频
    要多打上千次接口, 纯属浪费。

    输出格式: MultiIndex(date, code) 的 DataFrame, 含 weight 列。
    这正是 Panel.constituents() 期望的结构(它只看 weight 是否 notna)。
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
    # 代码统一成面板用的 .SH/.SZ 后缀, 否则 reindex 到 px.columns 会全部对不上
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
    一次性找出这批代码里聚宽认不出的那些(改名/退市多年/合并等历史遗留),
    用二分隔离而不是逐个试——几千只逐个试太慢, 而坏代码通常只有个位数。

    只用一个探测日期跑一次, 不是每天都重新隔离一遍: 一个代码"是否存在于
    聚宽的证券库里"跟具体哪天无关, 隔离一次的结果对整个更新区间都成立。

    batch_size 默认到函数体内才取 BATCH_SIZE —— 这个函数定义在 BATCH_SIZE
    赋值之前, 参数默认值若直接写 BATCH_SIZE 会在模块导入时立刻报
    NameError(默认值是定义时求值, 不是调用时)。
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
    stock_state.pkl 的增量更新。

    为什么必须单独做: update_price_panel 只更新价格, 状态表是另一个文件。
    两者不同步时 Panel.universe 会对状态掩码做 reindex().fillna(False),
    于是状态表覆盖不到的交易日可交易域全空, _trim_tail 直接把那几天砍掉
    —— 价格明明更新到了最新, 回测终点却停在状态表的最后一天, 而且看起来
    像是"最近没信号"。

    字段口径与既有文件保持一致:
      trade_status  中文状态字符串('交易'/'停牌一天'/...)
      status_t      1=当日可交易, 0=不可
      maxupdown     1=涨停 / -1=跌停 / 0=否
      isst/issts    ST / *ST 标记
      issusp        暂停上市
      lenlist       上市天数(自然日)
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
    # 聚宽对批量请求里任何一个无效代码都是整批直接报错, 不是跳过那一只
    # (老代码改名/退市多年/合并这类历史遗留很常见)。先用二分隔离一次,
    # 把坏代码摘出来, 后面 35 天都用干净的列表查, 不用每天都重新踩一遍雷。
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
    new = new.reindex(columns=old.columns)         # 列顺序/集合与旧文件严格一致
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
    """本项目唯一的远端源。HO_OFFLINE=1 或显式禁用时返回 None(纯本地)。"""
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

BATCH_SIZE = 2500  # 单次 get_price 查询的证券数上限, 留余量避免顶到接口限制


def _fetch_close_wide(jq, jq_codes: list[str], start: str, end: str, fq) -> pd.DataFrame:
    df = jq.get_price(jq_codes, start_date=start, end_date=end, frequency='daily',
                      fields=['close'], fq=fq, panel=False, skip_paused=False)
    if df is None or df.empty:
        return pd.DataFrame()
    return df.pivot(index='time', columns='code', values='close').sort_index()


def _fetch_in_batches(jq, jq_codes: list[str], start: str, end: str, fq,
                      batch_size: int = BATCH_SIZE) -> list[pd.DataFrame]:
    """
    分批拉取, 单批失败时二分裂开重试, 把坏代码隔离到最小范围。

    聚宽对整批请求里的任何一个无效代码都会直接报错(而不是跳过那一只),
    所以哪怕上游已经按上市/退市日期筛过, 仍可能有个别代码因为改名、转板、
    合并等原因失效。二分隔离保证一颗老鼠屎最多只坏掉它自己。
    """
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
    用真实上市/退市日期给每只股票分类, 而不是只看锚点日有没有价格——
    "还没上市"和"已经退市"在锚点日看起来都是 NaN, 分不清楚。若只按
    "锚点日 NaN"判定为新股去拉数据, 一只早就退市的股票会被当成新股
    重新灌入增量数据, 相当于让死掉的股票复活。

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
    """
    最后一个"已收盘"的交易日 —— 唯一的交易日历来源。

    本地 pkl 里只有已经落盘的历史交易日, 无法告诉你"今天算不算交易日"
    或者"上一个交易日是哪天", 所以这件事只能问聚宽。

    直接拿 today 当截止日会有两种取不到最新数据的情况:
      ① 今天是周末/节假日, 根本没有行情
      ② 今天是交易日但还没收盘(A股 15:00 收盘, 数据落库更晚),
         此时聚宽要么返回空, 要么返回一根不完整的日线
    所以走交易日历: 取日历上 <= 今天 的最后一个交易日; 如果它就是今天而
    当前时间还没到 15:30, 就再往前退一天。
    """
    jq = jq_auth()
    return _last_closed_trade_day(jq)


def _last_closed_trade_day(jq) -> pd.Timestamp:
    today = pd.Timestamp.today().normalize()
    try:
        days = jq.get_trade_days(end_date=str(today.date()), count=10)
    except Exception:
        return today          # 日历取不到就退回原行为, 不因此中断更新
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
    增量补齐 stock_price_adj_close.pkl 到最新交易日。

    复权拼接: 后复权价格的换算基准会随除权除息变化。直接拿聚宽今天算出的
    绝对后复权价去拼接旧文件, 一旦增量区间内有股票分红送股, 接缝处会出现
    跳变——不是真实涨跌, 是复权基准不一致导致的假信号。

    解决: 只用比率不用绝对值 —— 拉一段 **后复权** 价, 在这段内部算每日
    涨跌幅, 再在旧文件最后一天的价格上复利滚算。后复权序列在同一次请求内
    基准一致, 所以它的 pct_change 就是真实收益率; 而比率与基准无关, 接缝
    自然连续。

    这里绝不能用不复权价(fq=None)算涨跌幅: 不复权价在除权除息日会断崖下跌,
    那个跌幅不是收益率。用它 cumprod 会给每一只在增量区间内分红送股的股票
    灌进一个凭空的暴跌 —— 而"大量个股同时创新低"正是本策略条件②③的触发
    条件, 这种脏数据会直接伪造出信号。
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

    # 分类必须在拉取之前。面板里有历史遗留的早已退市代码(如 000003.SZ 深金田A,
    # 2002年退市), 聚宽会对整批请求报"无效的证券代码"而不是跳过那一只, 一颗
    # 老鼠屎坏一锅汤。先按真实上市/退市日期筛掉, 再去请求。
    # 证券基础信息走本地缓存, 一天之内重复运行不再调 API。
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

    # 只请求"正常"股票的涨跌幅; 新股在后面单独用后复权价补
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
            continue          # 退市股保持 NaN; 新股走下面的分支
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
