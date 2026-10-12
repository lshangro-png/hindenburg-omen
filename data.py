"""
数据层：读取本地数据、计算市场宽度指标，以及各级本地缓存。

    Panel         价格面板 + 每日可交易域（哪些股票当天计入统计）
    new_high_low  52 周新高 / 新低家数与占比（条件②③）
    mcclellan     McClellan 振荡器（条件④）
    BreadthCache  宽度指标的磁盘缓存
    LocalTable    小表缓存（证券基础信息等）
    IndexStore    指数收盘价的本地存储，缺口才去聚宽补

所有数据都是「先查本地，缺了才联网」，远端只有聚宽（见 jq_source.py）。
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
# 价格面板与可交易域
# =====================================================================

class Panel:
    """
    本地 pkl 面板数据（文件在 data_dir 下递归查找）：

        stock_price_adj_close.pkl   必需  后复权收盘价，index=日期，columns=股票
        stock_state.pkl             可选  状态长表 MultiIndex(日期, 股票)
        index_constituents_*.pkl    可选  指数成分股
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

    # ---------- 价格 ----------

    def _filter_columns(self, px: pd.DataFrame) -> pd.DataFrame:
        """
        只保留属于本策略股票池的列：
            ① 剔除非 6 位数字代码（如 T00018.SH 这类非普通股）
            ② 剔除 exclude_boards 里的交易所（默认北交所）
            ③ include_exchanges 非空时，只保留这些交易所
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
        out = px.drop(columns=drop)
        if out.shape[1] == 0:
            raise ValueError(
                f'过滤后没有任何股票剩下 (exclude_boards={self.exclude_boards}, '
                f'include_exchanges={self.include_exchanges}), 检查配置')
        return out

    @property
    def prices(self) -> pd.DataFrame:
        """过滤后的后复权收盘价（首次访问时读盘，转 float32 省一半内存）。"""
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
            if self.float32:
                px = px.astype('float32')
            self._px = px
        return self._px

    @property
    def last_date(self) -> pd.Timestamp:
        """面板最后一个交易日，作为默认回测终点。"""
        return self.prices.index[-1]

    # ---------- 状态表 ----------

    def _col(self, name: str) -> pd.DataFrame:
        """把状态长表的一列展开成宽表（日期 × 股票），并缓存。"""
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
        """按 state_rule 得到的可交易 bool 宽表；没有 stock_state.pkl 时返回 None。"""
        if self._mask is None:
            p = self.find('stock_state.pkl')
            if p is None:
                print('[Panel] 无 stock_state.pkl, 改用价格推断停牌 (见 inferred_mask)')
                self._mask = False
            else:
                self._raw = pd.read_pickle(p)
                self._cols = {}
                self._mask = self.state_rule(self._col)
                self._raw, self._cols = None, {}      # 用完释放内存
        return self._mask if self._mask is not False else None

    # ---------- 可交易域规则（col('列名') 取宽表）----------

    @staticmethod
    def rule_default(col):
        """
        当日有成交（status_t == 1）且上市满一年（lenlist ≥ 365 个自然日）。
        不剔 ST：研报口径是全市场，而且 ST 股是创新低的主力。
        """
        return ((col('status_t') == 1) & (col('lenlist') >= 365)).fillna(False)

    @staticmethod
    def rule_strict(col):
        """在 rule_default 基础上再剔除 ST 与 *ST。"""
        return ((col('status_t') == 1) & (col('lenlist') >= 365)
                & (col('isst') == 0) & (col('issts') == 0)).fillna(False)

    @staticmethod
    def rule_trade_status(col):
        """
        用 trade_status 字段判断，用于交叉验证。
        只认 '交易'（白名单）：停牌的写法有十几种，用黑名单容易漏。
        """
        ts = col('trade_status')
        return (ts.eq('交易') & (col('lenlist') >= 365)).fillna(False)

    @property
    def inferred_mask(self) -> pd.DataFrame:
        """
        没有状态表时，只凭价格推断可交易域：
            ① 当天有价格
            ② 累计已有 ≥ 252 个交易日的价格（约上市满一年）
            ③ 最近 5 天价格不是一动不动（排除长期停牌被前值填充）

        必须在完整面板上计算后再切片，否则 ② 的累计天数会从切片起点重新数。
        """
        if self._inferred is None:
            px = self.prices
            listed = px.notna()
            enough = listed.cumsum() >= 252
            frozen = (px.diff().abs().rolling(5).max() == 0).fillna(False)
            self._inferred = listed & enough & ~frozen
        return self._inferred

    # ---------- 股票池 ----------

    def universe(self, start: str, end: str, index_code: Optional[str] = None,
                 warmup: int = 260, min_coverage: float = 0.5
                 ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        返回对齐好的 (价格矩阵, 可交易 bool 矩阵)。

        warmup:     在 start 之前多取的交易日数（52 周滚动指标需要预热），
                    调用方拿到结果后自行裁到 start。
        index_code: 只保留该指数的成分股。
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
        # reindex 产生的 NaN 和 True/False 混在 object 里，先转 nullable boolean 再填充
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
        截掉尾部股票数骤降的交易日。

        状态表可能比价格面板少更新几天，这几天的可交易域会是空的，
        算出来「没有信号」其实是「没有数据」。判据是当日股票数低于
        中位数 × min_coverage。
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
        成分股 bool 宽表（日期 × 股票），名单前向填充到每个交易日。

        文件名 index_constituents_000300.SH.pkl 和 index_constituents_000300_SH.pkl
        都认；本地没有时自动从聚宽拉取。
        """
        stem = index_code.replace('.', '_')
        candidates = [f'index_constituents_{index_code}.pkl',
                      f'index_constituents_{stem}.pkl']
        p = next((f for f in (self.find(c) for c in candidates) if f), None)

        if p is None:
            p = self._fetch_constituents(index_code)

        raw = pd.read_pickle(p)
        if len(raw) == 0:
            raise ValueError(f'{p.name} 是空文件 (0 行), 该指数成分股需要重新下载')

        m = raw['weight'].notna().unstack(fill_value=False)
        m = m.reindex(self.prices.index).ffill()
        m = m.astype('boolean').fillna(False).astype(bool)
        return m

    def _fetch_constituents(self, index_code: str) -> Path:
        """本地没有成分股时从聚宽拉取并落盘；拉不到时给出可行的补救办法。"""
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
        # 起点用该指数对应策略的起点，避免多拉
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
        等权代理指数，只在拿不到真实指数时兜底使用（会打印警告）。
        单日收益截在 ±50%，避免停牌复牌的跨期涨跌带偏整条曲线。
        """
        ret = px.where(valid).pct_change(fill_method=None)
        ret = ret.clip(-0.5, 0.5).mean(axis=1).fillna(0)
        return (1 + ret).cumprod() * base


# =====================================================================
# 市场宽度指标
# =====================================================================

def new_high_low(px: pd.DataFrame, valid: pd.DataFrame,
                 window: int = 252, min_periods: int = 200) -> pd.DataFrame:
    """
    52 周新高 / 新低（条件②③），收盘价口径：
        新高 = 当日收盘价 ≥ 过去 window 日最高收盘价

    返回列：new_high, new_low, universe（当日股票数）, high_ratio, low_ratio

    注意：
      · 必须用收盘价算滚动极值；用最高价算极值再拿收盘价比较，新高会被严重低估。
      · min_periods < window 是为了容忍停牌，否则停过牌的股票复牌后一年内
        都不计新高新低，而它们正是创新低的主力。
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
    McClellan 振荡器（条件④）= EMA_short(净涨家数) − EMA_long(净涨家数)

    norm:
        raw   净涨家数 = 上涨家数 − 下跌家数（原始定义）
        rana  (上涨 − 下跌) / (上涨 + 下跌) × 1000，不受股票总数增长影响
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


# =====================================================================
# 本地缓存
# =====================================================================

class BreadthCache:
    """
    宽度指标的磁盘缓存（.cache/breadth_*.pkl）。

    缓存键包含全部重参数和源数据文件的修改时间，数据或参数一变自动失效。
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
            # 成分股文件两种命名都要试，和 Panel.constituents 一致
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
        """返回宽度指标表（已裁掉预热段）；缓存命中直接读盘，否则计算并写盘。"""
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


class LocalTable:
    """小表缓存（.cache/<name>.parquet），超过 max_age_days 才重新加载。"""

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
    指数收盘价的本地存储：每个指数一个 parquet + 一个 .meta.json。

    请求区间本地已覆盖就不联网；有缺口时只补缺口：
        尾部缺口  正常的增量更新
        头部缺口  多半是账号历史权限不足。拉不到就把最早可得日期记进
                  meta 的 head_floor，以后不再重试，免得每次都浪费额度。

    fresh_days: 本地最后一天距请求终点不超过这么多天，就算是最新的。
    """

    def __init__(self, store_dir: str = None, source=None,
                 fresh_days: int = 5, allow_remote: bool = True):
        self.dir = Path(store_dir or config.INDEX_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.source = source
        self.fresh_days = fresh_days
        self.allow_remote = allow_remote and not config.OFFLINE

    # ---------- 读写 ----------

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
        """返回 [start, end] 的收盘价序列，必要时先补缺口。"""
        local = self._read(code)
        req_start = pd.Timestamp(start)
        req_end = min(pd.Timestamp(end), pd.Timestamp.today().normalize())  # 终点不超过今天
        floor = self._meta(code).get('head_floor')
        floor = pd.Timestamp(floor) if floor else None

        # 找出缺口
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

        # 补缺口
        if not gaps:
            pass
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

        return local.loc[start:end]

    def list_stored(self) -> pd.DataFrame:
        """列出本地已存的指数及其覆盖区间。"""
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
