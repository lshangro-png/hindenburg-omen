"""
全部参数与策略定义。

参数分两类，区别在于改完之后的代价：
    轻参数  只用到已算好的中间序列，改完毫秒级出结果，可在参数实验室里拖滑块试
    重参数  要重新扫描全市场股票面板，改完需 clear-cache 后重跑 run（分钟级）

建议先在参数实验室里调轻参数，定下来再改重参数。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


# =====================================================================
# 参数
# =====================================================================

@dataclass
class Params:
    # ---------- 轻参数 ----------
    ma_period: int = 50            # ① 趋势均线周期
    ma_lookback: int = 5           # ① 均线与几天前比较（仅 ma_rising 用）
    trend_mode: str = 'ma_rising'  # ① 'ma_rising' 均线上升（美股原始定义）
                                   #   'above_ma'  价格在均线上方（浙商适配版）
    threshold: float = 0.022       # ② 新高、新低占比阈值
    hl_ratio: float = 2.0          # ③ 新高数 ≤ 新低数 × hl_ratio
    cluster_window: int = 36       # 集群窗口（交易日）
    cluster_min: int = 2           # 升级为有效集群的最少信号数
    cluster_mode: str = 'fixed'    # 'fixed' 窗口从首日固定 | 'chain' 链式延展
    dd_mode: str = 'peak_trough'   # 'peak_trough' 峰谷最大回撤 | 'from_signal' 相对起点跌幅
    dd_anchor: str = 'confirm'     # 'confirm' 从确认日起算（无前视）| 'first' 从首日起算（研报）
    grade_window: int = 252        # 定级用的回撤窗口
    dd_windows: tuple = (5, 21, 63, 252)        # 统计的回撤窗口：周 / 月 / 季 / 年
    grade_cuts: tuple = (-0.20, -0.10, -0.05)   # 较大回撤 / 中型回调 / 小幅回调 的分界

    # ---------- 重参数 ----------
    ema_short: int = 19            # ④ McClellan 快线
    ema_long: int = 39             # ④ McClellan 慢线
    mc_norm: str = 'raw'           # ④ 'raw' 涨跌家数差 | 'rana' 按家数归一化
    hl_window: int = 252           # ② 52 周新高新低窗口
    hl_min_periods: int = 200      # ② 窗口内最少有效天数（容忍停牌）
    warmup: int = 260              # 滚动指标预热天数
    exclude_boards: tuple = ('BJ',)  # 剔除的交易所后缀（北交所不在中证全指 / 上证综指内）
    include_exchanges: tuple = ()    # 只保留这些交易所，空 = 不限（上证综指设为 ('SH',)）
    min_coverage: float = 0.5      # 尾部交易日股票数低于中位数的这个比例就截掉

    def light(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k in LIGHT_KEYS}

    def heavy(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k in HEAVY_KEYS}


LIGHT_KEYS = {'ma_period', 'ma_lookback', 'trend_mode', 'threshold', 'hl_ratio',
              'cluster_window', 'cluster_min', 'cluster_mode', 'dd_mode',
              'dd_anchor', 'grade_window', 'dd_windows', 'grade_cuts'}
HEAVY_KEYS = {'ema_short', 'ema_long', 'mc_norm', 'hl_window',
              'hl_min_periods', 'warmup', 'exclude_boards', 'include_exchanges',
              'min_coverage'}

# 回测起点之前额外导出的指数天数，保证条件①的均线在首日就有值。
# 150 ≥ 滑块上限 ma_period 120 + ma_lookback 20。
INDEX_PREHEAT = 150

# 参数实验室的滑块
SLIDER_SPECS = [
    dict(key='ma_period', label='① 趋势均线周期', min=10, max=120, step=5, unit='日'),
    dict(key='ma_lookback', label='① 均线比较滞后', min=1, max=20, step=1, unit='日'),
    dict(key='threshold', label='② 新高/新低占比阈值', min=0.005, max=0.05, step=0.001,
         unit='', pct=True),
    dict(key='hl_ratio', label='③ 新高 ≤ 新低 ×', min=0.5, max=5.0, step=0.1, unit='倍'),
    dict(key='cluster_window', label='集群确认窗口', min=5, max=60, step=1, unit='日'),
    dict(key='cluster_min', label='升级为集群的最少信号数', min=1, max=6, step=1, unit='次'),
]


# =====================================================================
# 策略
# =====================================================================

@dataclass
class Strategy:
    name: str
    benchmark: str                          # 条件①和回撤用的指数
    state_rule: str = 'rule_default'        # 可交易域规则，见 data.Panel.rule_*
    universe_index: Optional[str] = None    # 只用该指数成分股，None = 全市场
    start: Optional[str] = None             # 回测起点，None = config.START
    params: Params = field(default_factory=Params)


# 原则：条件②③④的股票池要和条件①的指数是同一批股票。按指数类型有三种限定方式：
#     全市场      中证全指（剔除北交所后的全部 A 股）
#     按交易所    上证综指（沪市全部 A 股）→ include_exchanges=('SH',)
#     按成分股    沪深300 / 中证500 / 中证1000 → universe_index
#
# 五个策略统一用 rule_default（不剔 ST），口径一致才能互相比较。
#
# 注意：2.2% 阈值是按几千只股票定的。沪深300 只有 300 只成分股（2.2% ≈ 6.6 只），
# 占比跳动很大，规模指数的结果只适合和自己的历史比，不要和中证全指比命中率。

ASHARE = Strategy(
    name='中证全指', benchmark='000985.CSI', state_rule='rule_default',
    start='2005-01-01',   # 基日 2004-12-31；取不到的部分 IndexStore 会自动退到最早可得日期
    params=Params(),
)

GENERAL = Strategy(
    name='上证综指', benchmark='000001.SH', state_rule='rule_default',
    start='2000-01-01',   # 覆盖研报的 2000–2024 区间
    params=Params(include_exchanges=('SH',)),
)

# 规模指数的起点为各自的发布日（聚宽没有发布日之前的数据）
CSI300 = Strategy(
    name='沪深300', benchmark='000300.SH', state_rule='rule_default',
    universe_index='000300.SH', start='2005-04-08', params=Params(),
)
CSI500 = Strategy(
    name='中证500', benchmark='000905.SH', state_rule='rule_default',
    universe_index='000905.SH', start='2007-01-15', params=Params(),
)
CSI1000 = Strategy(
    name='中证1000', benchmark='000852.SH', state_rule='rule_default',
    universe_index='000852.SH', start='2014-10-17', params=Params(),
)

STRATEGIES = {'ashare': ASHARE, 'general': GENERAL,
              '300': CSI300, '500': CSI500, '1000': CSI1000}


# =====================================================================
# 运行设置（都可用环境变量覆盖）
# =====================================================================

import os  # noqa: E402

DATA_DIR = os.getenv('HO_DATA_DIR', 'data')          # 本地面板数据
START = os.getenv('HO_START', '2000-01-01')           # 策略没指定 start 时的起点
END = os.getenv('HO_END') or None                     # None = 面板最后一个交易日
CACHE_DIR = os.getenv('HO_CACHE_DIR', '.cache')       # 宽度指标缓存
INDEX_DIR = os.getenv('HO_INDEX_DIR', 'index_store')  # 指数收盘价
OUT_DIR = os.getenv('HO_OUT_DIR', 'output')           # 结果输出
OFFLINE = os.getenv('HO_OFFLINE', '').lower() in ('1', 'true', 'yes')  # 强制不联网

# 聚宽额度保护（单位：行）
JQ_RESERVE = int(os.getenv('JQ_RESERVE', 50_000))                 # 至少给别人留这么多
JQ_MAX_ROWS = int(os.getenv('JQ_MAX_ROWS', 20_000))               # 单次运行更新指数的上限
JQ_MAX_ROWS_PANEL = int(os.getenv('JQ_MAX_ROWS_PANEL', 500_000))  # 单次运行更新面板的上限
JQ_DRY_RUN = os.getenv('JQ_DRY_RUN', '') == '1'                   # 只打印计划，不真拉
