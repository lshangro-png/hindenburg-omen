"""
data.py —— 数据层

    Panel        本地 pkl 价格面板 + 每日可交易域
    breadth      52周新高新低 / McClellan
    BreadthCache 宽度指标磁盘缓存
    IndexStore   基准指数本地 parquet
    LocalTable   通用小表本地缓存(证券基础信息等)

本地优先原则贯穿全部四类数据, 顺序一律是"先查本地, 缺了才联网":
    价格面板  本地 pkl, 只有 `hindenburg.py update-panel` 会主动联网增量补
    指数收盘  本地 index_store/*.parquet, 只补缺口, 缺口拿不到会记进 meta 不再重试
    宽度指标  本地 .cache/breadth_*.pkl, 键含全部参数与源文件 mtime
    证券信息  本地 .cache/securities.parquet, 隔日才刷新
远端数据源只有聚宽一个(见 jq_source.py)。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional, Callable

import numpy as np
import pandas as pd

import config


# =====================================================================
# 面板数据
# =====================================================================

class Panel:
    """
    本地 pkl 面板数据

    必需: stock_price_adj_close.pkl   后复权收盘价 (index=日期, columns=股票)
    可选: stock_state.pkl             状态长表 MultiIndex(日期,股票)
          index_constituents_*.pkl    成分股权重
    文件在 data_dir 下递归查找, 放子目录也能找到。
    """

    def __init__(self, data_dir: str = None, state_rule: str | Callable = 'rule_default',
                 float32: bool = True, exclude_boards: tuple = ('BJ',),
                 include_exchanges: tuple = ()):
        self.dir = Path(data_dir or config.DATA_DIR)
        if not self.dir.exists():
            raise FileNotFoundError(f'数据目录不存在: {self.dir.resolve()}')
        self.state_rule = (getattr(Panel, state_rule) if isinstance(state_rule, str)
                           else state_rule)
        self.float32 = float32
        self.exclude_boards = tuple(exclude_boards or ())
        self.include_exchanges = tuple(include_exchanges or ())
        self._px = None
        self._mask = None
        self._inferred = None
        self._raw = None
        self._cols = {}

    # ---------- 文件定位 ----------

    def find(self, name: str) -> Optional[Path]:
        p = self.dir / name
        if p.exists():
            return p
        hits = list(self.dir.rglob(name))
        return hits[0] if hits else None

    # ---------- 列过滤 ----------

    def _filter_columns(self, px: pd.DataFrame) -> pd.DataFrame:
        """
        剔掉不该进该策略"股票池"的列:

          ① 非 6 位数字代码 —— 面板里混着 T00018.SH 这种非普通股标的
          ② exclude_boards 指定的交易所 —— 默认剔北交所
          ③ include_exchanges 非空时, 只保留白名单里的交易所

        为什么必须过滤: 条件②③④(宽度)的股票池要和条件①(趋势)的指数是同一批
        标的, 否则两边说的不是同一个市场。上证综指只含沪市约2300只, 而全市场
        有5200只 —— 不限定的话超过一半的宽度来自指数外的深市个股。
        有固定成分名单的指数(300/500/1000)走 universe() 里的 constituents(),
        比按交易所切更精确。
        """
        drop_bad = [c for c in px.columns if not str(c)[:6].isdigit()]
        drop_board = [c for c in px.columns
                      if str(c).rsplit('.', 1)[-1].upper() in self.exclude_boards]
        drop_exch = ([c for c in px.columns
                      if str(c).rsplit('.', 1)[-1].upper() not in self.include_exchanges]
                     if self.include_exchanges else [])
        drop = sorted(set(drop_bad) | set(drop_board) | set(drop_exch))
        if not drop:
            return px
        msg = []
        if drop_bad:
            msg.append(f'非股票代码 {len(drop_bad)} 只 (如 {drop_bad[:3]})')
        if drop_board:
            msg.append(f'{"/".join(self.exclude_boards)} 板块 {len(drop_board)} 只')
        if drop_exch:
            msg.append(f'非 {"/".join(self.include_exchanges)} 交易所 {len(drop_exch)} 只')
        out = px.drop(columns=drop)
        if out.shape[1] == 0:
            raise ValueError(
                f'过滤后没有任何股票剩下 (exclude_boards={self.exclude_boards}, '
                f'include_exchanges={self.include_exchanges}), 检查配置')
        return out

    # ---------- 价格 ----------

    @property
    def prices(self) -> pd.DataFrame:
        if self._px is None:
            p = self.find('stock_price_adj_close.pkl')
            if p is None:
                found = sorted(x.relative_to(self.dir) for x in self.dir.rglob('*.pkl'))
                raise FileNotFoundError(
                    f'{self.dir.resolve()} 下找不到 stock_price_adj_close.pkl\n'
                    + ('实际存在:\n' + '\n'.join(f'  - {f}' for f in found)
                       if found else '该目录没有任何 .pkl'))
            px = pd.read_pickle(p)
            px.index = pd.to_datetime(px.index)
            px = self._filter_columns(px)
            # 6000+ 只 x 6000+ 天的 float64 约 300MB, 降到 float32 省一半。
            # 滚动极值只做比较, 精度足够。
            if self.float32:
                px = px.astype('float32')
            self._px = px
        return self._px

    @property
    def last_date(self) -> pd.Timestamp:
        """面板最后一个交易日 —— 用它当回测终点, 避免把 END 写成未来日期"""
        return self.prices.index[-1]

    # ---------- 状态 ----------

    def _col(self, name: str) -> pd.DataFrame:
        """按列名展开状态长表为宽表。只展开规则用到的列, 展开前降位宽以省内存。"""
        if name in self._cols:
            return self._cols[name]
        if name not in self._raw.columns:
            raise KeyError(f'stock_state.pkl 无列 "{name}", 可用: {list(self._raw.columns)}')

        s = self._raw[name]
        if pd.api.types.is_integer_dtype(s) and s.abs().max() < 128:
            s = s.astype('int8')
        elif pd.api.types.is_object_dtype(s):
            s = s.astype('category')

        wide = s.unstack()
        wide.index = pd.to_datetime(wide.index)
        self._cols[name] = wide
        return wide

    @property
    def state_mask(self) -> Optional[pd.DataFrame]:
        """可交易 bool 宽表; 无 stock_state.pkl 时返回 None"""
        if self._mask is None:
            p = self.find('stock_state.pkl')
            if p is None:
                print('[Panel] 无 stock_state.pkl, 改用价格推断停牌 (见 inferred_mask)')
                self._mask = False
            else:
                self._raw = pd.read_pickle(p)
                self._cols = {}
                self._mask = self.state_rule(self._col)
                self._raw, self._cols = None, {}      # 用完释放
        return self._mask if self._mask is not False else None

    # ---------- 可交易域规则 ----------
    # col 是取列函数, 用法 col('status_t')

    @staticmethod
    def rule_default(col):
        """
        默认: 当日有成交 + 上市满一年

        status_t == 1  当日有成交 (含盘中短时停牌, 这些仍有真实收盘价)
        lenlist        上市自然日数 —— 是自然日不是交易日, 52周需 >= 365

        不剔 ST/*ST: 研报口径为全市场, 且 ST 是创新低主力。
        """
        return ((col('status_t') == 1) & (col('lenlist') >= 365)).fillna(False)

    @staticmethod
    def rule_strict(col):
        """额外剔除 ST 与 *ST"""
        return ((col('status_t') == 1) & (col('lenlist') >= 365)
                & (col('isst') == 0) & (col('issts') == 0)).fillna(False)

    @staticmethod
    def rule_trade_status(col):
        """
        改用 trade_status 字符串判定, 用于交叉验证

        用白名单('交易')而非黑名单: 实测该列有 12 种非"交易"取值(停牌一天/
        停牌1小时/停牌半天/盘中停牌/暂停上市/...), 逐个列举容易漏, 且新出现
        的停牌措辞会静默漏判。
        """
        ts = col('trade_status')
        return (ts.eq('交易') & (col('lenlist') >= 365)).fillna(False)

    # ---------- 无状态表时的退化推断 ----------

    @property
    def inferred_mask(self) -> pd.DataFrame:
        """
        无 stock_state.pkl 时, 仅凭价格判定可交易域:
            ① 有价格
            ② 累计已出现的交易日数 >= 252 (上市满一年, 够 52 周窗口)
            ③ 近 5 日价格不是完全不动 (长期停牌被前值填充)

        必须在完整面板上算再切片。若对切片后的价格做 cumsum, 计数会从切片
        起点重新开始, 结果是预热段全体不可交易 —— 那预热就白留了。

        ② 用 cumsum 而非 rolling(252).sum(): cumsum 单调, 语义是"上市至今
        已有多少个交易日", 与 lenlist 口径一致; rolling 在退市转 NaN 后会
        把之前的历史一起作废。
        """
        if self._inferred is None:
            px = self.prices
            listed = px.notna()
            enough = listed.cumsum() >= 252
            frozen = (px.diff().abs().rolling(5).max() == 0).fillna(False)
            self._inferred = listed & enough & ~frozen
        return self._inferred

    # ---------- 可交易域 ----------

    def universe(self, start: str, end: str, index_code: Optional[str] = None,
                 warmup: int = 260, min_coverage: float = 0.5
                 ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        返回 (价格矩阵, 可交易bool掩码), 两者已对齐。

        warmup: 在 start 之前额外多取的交易日数。52周滚动极值需要 252 天预热,
        不留预热期的话回测头一年的 new_high/new_low 全是 NaN, 条件②恒 False,
        等于白白丢掉一年样本。调用方拿到结果后自行裁到 start。
        """
        px_all = self.prices
        i0 = px_all.index.searchsorted(pd.Timestamp(start))
        real_start = px_all.index[max(0, i0 - warmup)]
        px = px_all.loc[real_start:end]
        if len(px) == 0:
            raise ValueError(f'区间 {start}~{end} 内没有任何交易日')
        listed = px.notna()

        mask = self.state_mask if self.state_mask is not None else self.inferred_mask
        m = mask.reindex(index=px.index, columns=px.columns)
        # infer_objects() 对这里没用: reindex 引入的 NaN 和原有的 True/False
        # 混在同一个 object 数组里, numpy 的 bool 类型装不下 NaN, 推断不出
        # 单一类型, infer_objects 转完还是 object, 后面 fillna 照样触发警告。
        # 真正有效的做法: 先转成 pandas 的 nullable boolean(能装 NA), fillna
        # 后再转回普通 bool —— 每一步类型都明确, 不需要靠隐式推断。
        valid = listed & m.astype('boolean').fillna(False).astype(bool)

        if index_code:
            cm = self.constituents(index_code).reindex(index=px.index, columns=px.columns)
            valid &= cm.astype('boolean').fillna(False).astype(bool)

        px, valid = self._trim_tail(px, valid, min_coverage)

        return px, valid

    @staticmethod
    def _trim_tail(px: pd.DataFrame, valid: pd.DataFrame, min_coverage: float
                   ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        截掉尾部可交易域塌陷的交易日。

        为什么必须有这一步: stock_state.pkl 的最后一天可能早于价格面板的最后
        一天(状态表和价格面板是两次更新, 不保证同步)。universe() 里对状态掩码
        做 reindex().fillna(False), 于是状态表覆盖不到的那些交易日 valid 全为
        False —— 可交易域是空的, new_high/new_low 恒 0, high_ratio 是 NaN,
        信号恒 False。这不是"市场没信号", 是数据没到, 但两者在输出里长得一模
        一样, 会被当成真实结论读。

        判据用"相对中位数的覆盖率"而不是"是否为 0": 状态表边界附近往往是先
        塌到很小再归零, 只查 0 会漏掉塌了一半的那几天。
        """
        n = valid.sum(axis=1)
        if not len(n) or n.max() == 0:
            raise ValueError('可交易域全为空, 检查 stock_state.pkl 与价格面板是否对得上')
        med = n[n > 0].median()
        ok = n >= med * min_coverage
        if ok.all():
            return px, valid
        last_ok = ok[ok].index.max()
        dropped = px.index[px.index > last_ok]
        if len(dropped):
            print(f'[Panel] ⚠ 尾部 {len(dropped)} 个交易日可交易域塌陷 '
                  f'({dropped[0]:%Y-%m-%d} ~ {dropped[-1]:%Y-%m-%d}, '
                  f'期末仅 {n.iloc[-1]:.0f} 只 vs 中位 {med:.0f} 只), 已截断。')
            print('       多半是 stock_state.pkl 落后于价格面板, '
                  '两个文件要一起更新才能覆盖到最新交易日。')
        return px.loc[:last_ok], valid.loc[:last_ok]

    def constituents(self, index_code: str) -> pd.DataFrame:
        """
        成分股 bool 矩阵。名单只在调仓日更新, 需前向填充到每个交易日。

        文件名两种写法都认: 保留原点号的 index_constituents_000300.SH.pkl,
        和把点换成下划线的 index_constituents_000300_SH.pkl。不同来源导出的
        命名习惯不一样, 只支持一种会让"文件明明在那儿却报找不到"。
        """
        stem = index_code.replace('.', '_')
        candidates = [f'index_constituents_{index_code}.pkl',   # 000300.SH
                      f'index_constituents_{stem}.pkl']          # 000300_SH
        p = next((f for f in (self.find(c) for c in candidates) if f), None)

        if p is None:
            # 本地没有就直接去聚宽拉一次并落盘, 而不是抛错让整个策略失败。
            # 成分股是"一次性补齐"的静态数据, 缺了就补, 没有理由要求用户
            # 先手动跑一条命令、看到报错、再跑第二条。
            p = self._fetch_constituents(index_code)

        raw = pd.read_pickle(p)
        if len(raw) == 0:
            raise ValueError(f'{p.name} 是空文件 (0 行), 该指数成分股需要重新下载')

        m = raw['weight'].notna().unstack(fill_value=False)
        m = m.reindex(self.prices.index).ffill()
        m = m.astype('boolean').fillna(False).astype(bool)
        return m

    def _fetch_constituents(self, index_code: str) -> Path:
        """本地缺成分股时的自动补齐。拉不到就把可行的补救办法一次说清楚。"""
        avail = sorted({x.stem.replace('index_constituents_', '')
                        for x in self.dir.rglob('index_constituents_*.pkl')})
        if config.OFFLINE:
            raise FileNotFoundError(
                f'本地没有 {index_code} 的成分股, 且 HO_OFFLINE=1 不允许联网。\n'
                f'  本地已有: {avail}')
        try:
            import jq_source
        except ImportError as e:
            raise FileNotFoundError(f'本地没有 {index_code} 的成分股, 也无法导入 jq_source: {e}')

        print(f'[Panel] 本地无 {index_code} 成分股, 从聚宽拉取…')
        # 起点跟着该指数所属策略走, 拉多了纯属浪费配额
        st = next((s for s in config.STRATEGIES.values()
                   if s.universe_index == index_code), None)
        start = (st.start if st else None) or config.START
        end = self.last_date.strftime('%Y-%m-%d')
        try:
            return jq_source.fetch_constituents(index_code, start, end, out_dir=str(self.dir))
        except Exception as e:
            raise FileNotFoundError(
                f'{index_code} 成分股本地没有, 从聚宽拉取也失败: {e}\n'
                f'  本地已有: {avail}\n'
                f'  可先单独试: python hindenburg.py fetch-constituents {index_code}')

    @staticmethod
    def equal_weight_index(px: pd.DataFrame, valid: pd.DataFrame,
                           base: float = 1000.0) -> pd.Series:
        """
        等权代理指数 —— 本地/远端都拿不到真实指数时的兜底。
        条件①只判断 MA50 方向, 对编制方式不敏感, 但口径与研报不同, 会打印警告。
        """
        ret = px.where(valid).pct_change(fill_method=None)
        # 停牌复牌造成的跨期收益会被算成单日收益, 截尾避免个别票带偏整条曲线
        ret = ret.clip(-0.5, 0.5).mean(axis=1).fillna(0)
        return (1 + ret).cumprod() * base


# =====================================================================
# 市场宽度
# =====================================================================

def new_high_low(px: pd.DataFrame, valid: pd.DataFrame,
                 window: int = 252, min_periods: int = 200) -> pd.DataFrame:
    """
    52周新高/新低, 收盘价口径 (与研报公式一致):
        p_hi = #{i : P_it = max_252 P_i} / N_t

    必须用收盘价算滚动极值。若用最高价算 max 却拿收盘价比较,
    因 high >= close 恒成立, 新高数会被系统性低估到接近 0。

    min_periods < window 是刻意的: A股停牌普遍, 要求窗口内 252 个非 NaN
    会让所有停过牌的票在复牌后一年内永远不计新高新低, 而这批票恰恰是
    创新低的主力, 会系统性压低 low_ratio 进而压低条件②的触发率。
    """
    roll = px.rolling(window, min_periods=min_periods)
    hi = ((px >= roll.max()) & valid).sum(axis=1)
    lo = ((px <= roll.min()) & valid).sum(axis=1)
    n = valid.sum(axis=1)
    return pd.DataFrame({
        'new_high': hi, 'new_low': lo, 'universe': n,
        'high_ratio': hi / n.replace(0, np.nan),
        'low_ratio': lo / n.replace(0, np.nan),
    })


def mcclellan(px: pd.DataFrame, valid: pd.DataFrame,
              short: int = 19, long: int = 39, norm: str = 'raw') -> pd.Series:
    """
    McClellan 振荡器 = EMA19(净涨跌) - EMA39(净涨跌)

    norm='raw'   净涨跌 = A - D             与研报/原始定义一致
    norm='rana'  净涨跌 = (A-D)/(A+D)*1000  比例调整版
    A股可交易域从 2011 年约 2000 只涨到 2026 年约 5000 只, raw 口径的量纲
    随标的数漂移。条件④只看符号, 对缩放不敏感, 但长样本上 rana 更干净,
    建议至少用 rana 做一次稳健性对照。
    """
    ret = px.pct_change(fill_method=None)
    a = ((ret > 0) & valid).sum(axis=1)
    d = ((ret < 0) & valid).sum(axis=1)
    net = a - d
    if norm == 'rana':
        net = (net / (a + d).replace(0, np.nan) * 1000).fillna(0)
    elif norm != 'raw':
        raise ValueError(f'norm 只能是 raw 或 rana, 收到 {norm!r}')
    return (net.ewm(span=short, adjust=False).mean()
            - net.ewm(span=long, adjust=False).mean())


class BreadthCache:
    """
    宽度指标缓存 —— 本地优先的第三环

    展开千万行状态表 + 对大矩阵做 252 日滚动极值是主要耗时, 产出却只有几千行。
    缓存键含全部重参数与源文件 mtime, 数据或参数一变自动失效, 不会拿到陈旧结果。
    """

    def __init__(self, cache_dir: str = None):
        self.dir = Path(cache_dir or config.CACHE_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _key(self, panel: Panel, start, end, index_code, **params) -> str:
        parts = [str(start), str(end), str(index_code),
                 getattr(panel.state_rule, '__name__', 'custom')]
        parts += [f'{k}={v}' for k, v in sorted(params.items())]
        for fn in ('stock_price_adj_close.pkl', 'stock_state.pkl'):
            p = panel.find(fn)
            parts.append(f'{fn}:{p.stat().st_mtime_ns if p else 0}')
        if index_code:
            # 两种命名都试, 和 Panel.constituents 保持一致。只认一种的话,
            # 用另一种命名的用户会让 mtime 恒为 0 —— 换了成分股文件缓存
            # 也不失效, 静默拿到旧口径的宽度序列。
            p = (panel.find(f'index_constituents_{index_code}.pkl')
                 or panel.find(f"index_constituents_{index_code.replace('.', '_')}.pkl"))
            parts.append(f'cons:{p.stat().st_mtime_ns if p else 0}')
        return hashlib.md5('|'.join(parts).encode()).hexdigest()[:16]

    def get(self, panel: Panel, start: str, end: str, index_code: Optional[str] = None,
            *, ema_short: int = 19, ema_long: int = 39, warmup: int = 260,
            hl_window: int = 252, hl_min_periods: int = 200,
            mc_norm: str = 'raw', min_coverage: float = 0.5,
            exclude_boards: tuple = ('BJ',),
            include_exchanges: tuple = ()) -> pd.DataFrame:
        # exclude_boards / include_exchanges 影响的是 panel.prices 的列集合,
        # 缓存键必须带上它们, 否则改了股票池会命中旧缓存, 拿到口径不符的
        # 陈旧宽度序列 —— 而且不会有任何报错。
        key = self._key(panel, start, end, index_code,
                        es=ema_short, el=ema_long, w=warmup,
                        win=hl_window, mp=hl_min_periods, mc=mc_norm,
                        cov=min_coverage, xb=','.join(sorted(exclude_boards or ())),
                        ie=','.join(sorted(include_exchanges or ())))
        path = self.dir / f'breadth_{key}.pkl'
        if path.exists():
            df = pd.read_pickle(path)
            return df

        px, valid = panel.universe(start, end, index_code, warmup=warmup,
                                   min_coverage=min_coverage)
        df = new_high_low(px, valid, hl_window, hl_min_periods)
        df['mcclellan'] = mcclellan(px, valid, ema_short, ema_long, mc_norm)
        df['eq_weight_index'] = Panel.equal_weight_index(px, valid)
        df = df.loc[str(start):]          # 裁掉预热段
        df.to_pickle(path)
        return df

    def clear(self):
        n = 0
        for f in self.dir.glob('breadth_*.pkl'):
            f.unlink()
            n += 1
        print(f'[Cache] 已清空 {n} 个宽度缓存文件')


# =====================================================================
# 通用小表本地缓存
# =====================================================================

class LocalTable:
    """
    小表本地缓存(证券基础信息等) —— 本地优先的第四环

    证券上市/退市日期这种表, 一天之内不会变, 但每次 update-panel 都要用来
    区分"新股"与"退市股"。不缓存的话每跑一次就白调一次 API。
    """

    def __init__(self, cache_dir: str = None):
        self.dir = Path(cache_dir or config.CACHE_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)

    def get(self, name: str, loader: Callable[[], pd.DataFrame],
            max_age_days: int = 1) -> pd.DataFrame:
        path = self.dir / f'{name}.parquet'
        if path.exists():
            age = (pd.Timestamp.now() - pd.Timestamp(path.stat().st_mtime, unit='s')).days
            if age < max_age_days:
                print(f'[LocalTable] {name} 本地命中 (缓存 {age} 天前)')
                return pd.read_parquet(path)
        df = loader()
        try:
            df.to_parquet(path)
            print(f'[LocalTable] {name} 已刷新并落盘')
        except Exception as e:
            print(f'[LocalTable] {name} 落盘失败(不影响本次使用): {e}')
        return df


# =====================================================================
# 指数存储
# =====================================================================

class IndexStore:
    """
    基准指数本地存储 —— 本地优先的第二环

    每个指数一个 parquet + 一个 .meta.json。请求区间被本地覆盖时完全不碰
    远端。缺口分头尾两段分别处理:

      尾部缺口  正常增量更新, 每次几行。
      头部缺口  多为账号历史年限权限不足。拉一次拿不到就把实际可得的
                最早日期写进 meta.head_floor, 之后不再重试 —— 否则每跑
                一次回测就白烧一次配额。

    fresh_days: 本地末日期距请求末日期在这个天数内就算新鲜, 不去更新。
    """

    def __init__(self, store_dir: str = None, source=None,
                 fresh_days: int = 5, allow_remote: bool = True):
        self.dir = Path(store_dir or config.INDEX_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.source = source
        self.fresh_days = fresh_days
        self.allow_remote = allow_remote and not config.OFFLINE

    # ---------- 落盘 ----------

    def _path(self, code: str) -> Path:
        return self.dir / f"{code.replace('.', '_')}.parquet"

    def _meta_path(self, code: str) -> Path:
        return self.dir / f"{code.replace('.', '_')}.meta.json"

    def _meta(self, code: str) -> dict:
        p = self._meta_path(code)
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                return {}
        return {}

    def _save_meta(self, code: str, **kw):
        m = self._meta(code)
        m.update(kw)
        self._meta_path(code).write_text(json.dumps(m, ensure_ascii=False, indent=1))

    def _read(self, code: str) -> Optional[pd.Series]:
        p = self._path(code)
        if not p.exists():
            return None
        df = pd.read_parquet(p)
        s = pd.Series(df['close'].values,
                      index=pd.to_datetime(df.index), name=code).sort_index()
        return s if len(s) else None

    def _write(self, code: str, s: pd.Series):
        s = s.dropna().sort_index()
        s = s[~s.index.duplicated(keep='last')]
        if len(s):
            pd.DataFrame({'close': s.values}, index=s.index).to_parquet(self._path(code))

    # ---------- 取数 ----------

    def get(self, code: str, start: str, end: str) -> pd.Series:
        local = self._read(code)
        req_start = pd.Timestamp(start)
        # 请求终点不可能超过今天; END 写成未来日期时不该因此永远判定"不新鲜"
        req_end = min(pd.Timestamp(end), pd.Timestamp.today().normalize())
        floor = self._meta(code).get('head_floor')
        floor = pd.Timestamp(floor) if floor else None

        gaps = []
        if local is None:
            gaps.append((req_start, req_end, 'full'))
        else:
            lo, hi = local.index.min(), local.index.max()
            want_head = req_start if floor is None else max(req_start, floor)
            if lo > want_head + pd.Timedelta(days=30):
                gaps.append((want_head, lo - pd.Timedelta(days=1), 'head'))
            if hi < req_end - pd.Timedelta(days=self.fresh_days):
                gaps.append((hi + pd.Timedelta(days=1), req_end, 'tail'))

        if not gaps:
            pass          # 本地已完整, 不必吭声
        elif self.source is None or not self.allow_remote:
            if local is None:
                raise ValueError(
                    f'{code} 本地无数据且未启用远端。配好 JQ_USER/JQ_PASSWORD 后跑一次 '
                    f'`python hindenburg.py update` 即可, 指数日线约 250 行/年, 消耗很小。')
            print(f'[IndexStore] {code} 有缺口但未启用远端, 使用现有 {len(local)} 行')
        else:
            kind_cn = {'head': '头部', 'tail': '尾部', 'full': '全量'}
            for gs, ge, kind in gaps:
                print(f'[IndexStore] {code} 补{kind_cn[kind]} {gs:%Y-%m-%d} ~ {ge:%Y-%m-%d}')
                try:
                    fresh = self.source.fetch(code, gs.strftime('%Y-%m-%d'),
                                              ge.strftime('%Y-%m-%d'))
                except Exception as e:
                    print(f'  ⚠ 拉取失败: {e}')
                    if local is None:
                        raise
                    continue
                if fresh is None or not len(fresh):
                    if kind in ('head', 'full') and local is not None:
                        self._save_meta(code, head_floor=str(local.index.min().date()),
                                        note='头部缺口拉取为空, 已记录不再重试')
                        print('  该段无数据, 已写入 meta, 后续不再重试')
                    continue
                local = fresh if local is None else pd.concat([local, fresh])
                self._write(code, local)
                local = self._read(code)

            if local is not None and local.index.min() > req_start + pd.Timedelta(days=30):
                self._save_meta(code, head_floor=str(local.index.min().date()))

        if local is None or not len(local):
            raise ValueError(f'{code} 最终没有任何数据')

        # 起点晚于请求不再警告: Omen.compute 会把指数缺失的头部整段裁掉,
        # 回测实际就是从有数据的第一天开始, 不存在"条件①失效"的区间。
        return local.loc[start:end]

    def list_stored(self) -> pd.DataFrame:
        rows = []
        for p in sorted(self.dir.glob('*.parquet')):
            df = pd.read_parquet(p)
            idx = pd.to_datetime(df.index)
            code = p.stem.replace('_', '.')
            rows.append({'指数': code, '行数': len(df),
                         '起始': str(idx.min().date()), '结束': str(idx.max().date()),
                         'head_floor': self._meta(code).get('head_floor', '')})
        return pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=['指数', '行数', '起始', '结束', 'head_floor'])
