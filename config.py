"""
config.py —— 全部超参数集中在这里

参数分两层, 这个划分不是随意的, 它决定了改完参数要付出什么代价:

  轻参数 (LIGHT)  只依赖已算好的中间序列(指数收盘、新高新低占比、家数、
                  McClellan值)。改这些不需要碰股票面板, 毫秒级出结果,
                  可以在"参数实验室"HTML里拖滑块实时预览。

  重参数 (HEAVY)  必须重新扫描全市场股票面板才能算出新的中间序列。
                  改这些要重跑 `python hindenburg.py run`, 分钟级。

调参建议: 先在参数实验室里把轻参数调顺(免费), 定下来之后再动重参数(昂贵)。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


# =====================================================================
# 参数
# =====================================================================

@dataclass
class Params:
    # ---------- 轻参数: 可在参数实验室实时预览 ----------
    ma_period: int = 50           # 条件① 趋势均线周期
    ma_lookback: int = 5          # 条件① 均线比较滞后期(仅 trend_mode='ma_rising' 时用到)
    trend_mode: str = 'ma_rising'  # 条件① 怎么判定"趋势向上":
                                  #   ma_rising 均线本身在上升: MA(t) > MA(t-ma_lookback)
                                  #             美股原始定义("十周移动平均线呈现上升趋势"),
                                  #             当前默认 —— 实测过 above_ma 在 2015 年那类
                                  #             闪崩行情里同样捕捉不到信号(价格跌速远超均线
                                  #             反应速度, 顶部到条件②满足之间价格已经把均线
                                  #             甩开几十个百分点, 两种口径殊途同归), 换成
                                  #             above_ma 换不来实质收益, 不如贴合原始定义。
                                  #   above_ma  指数站在均线上方: Close(t) > MA(t)
                                  #             浙商 A股适配版口径, 保留作为可选对照。
    threshold: float = 0.022      # 条件② 新高/新低占比阈值 (研报口径 2.2%)
    hl_ratio: float = 2.0         # 条件③ 新高数 ≤ 新低数 × 该倍数
    cluster_window: int = 36      # 集群确认窗口(交易日)
    cluster_min: int = 2          # 升级为"有效集群"的最少信号数
    cluster_mode: str = 'fixed'   # 'fixed'=窗口从首日固定(贴研报字面口径); 'chain'=链式延展
    dd_mode: str = 'peak_trough'  # 研报口径: 窗口内峰谷最大回撤; 'from_signal'=信号日收盘起最低点
    dd_anchor: str = 'confirm'    # 回撤起算点: 'confirm'=集群确认日(无前视) | 'first'=集群首日(研报口径,有前视)
    grade_window: int = 252       # 定级用的回撤窗口 (1年)
    dd_windows: tuple = (5, 21, 63, 252)          # 研报口径: 1周/1月/1季/1年
    grade_cuts: tuple = (-0.20, -0.10, -0.05)     # 较大回撤/中型回调/小幅回调 分界

    # ---------- 重参数: 改动需重跑回测 ----------
    ema_short: int = 19           # McClellan 快线
    ema_long: int = 39            # McClellan 慢线
    mc_norm: str = 'raw'          # McClellan 口径: 'raw' | 'rana'(按家数归一化)
    hl_window: int = 252          # 52周新高新低的滚动窗口
    hl_min_periods: int = 200     # 容忍停牌造成的窗口缺失(<hl_window 即为放宽)
    warmup: int = 260             # 滚动指标预热交易日数
    exclude_boards: tuple = ('BJ',)   # 排除的交易所后缀。北交所不在中证全指/上证综指成分内,
                                      # 留着会让条件②③④的口径与条件①的趋势标的对不上
    include_exchanges: tuple = ()     # 只保留这些交易所的股票, 空=不限制。
                                      # 上证综指只含沪市股票, 所以它的宽度池必须
                                      # 限定 ('SH',), 否则 56% 的宽度来自指数外的深市个股。
                                      # 有固定成分名单的指数(300/500/1000)用
                                      # Strategy.universe_index 更精确, 不走这条。
    min_coverage: float = 0.5     # 可交易域相对中位数的覆盖率下限, 低于此的尾部交易日直接截掉

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

# 条件① 的均线需要 index_close 在回测起点之前就有值, 否则头 ma_period+ma_lookback
# 个交易日 cond1 恒为 False。导出给参数实验室时也要带上这段, 否则浏览器里
# 改 ma_period 会算出跟 Python 不一样的结果。150 覆盖滑块上限 120+20。
INDEX_PREHEAT = 150

# 参数实验室里滑块的取值范围与说明 (只覆盖轻参数中适合连续调节的那些)
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

# 研报只报命中率, 不给对照组。但 A股 252 交易日窗口内出现 ≥10% 峰谷回撤的
# 无条件概率约 97%, 所以"命中率 100%"这种数字本身不含信息量。
# summarize() 因此额外算两个对照:
#   baseline_all      全样本每一天当作信号日的回撤分布
#   baseline_uptrend  只在条件①(趋势向上)成立的日子里抽样 —— 剔掉趋势过滤
#                     本身的贡献后, 条件②③④还剩多少增量
# 研报口径的命中率仍原样保留, 对照组只是并列展示, 不覆盖原指标。

@dataclass
class Strategy:
    name: str
    benchmark: str                          # 条件①的基准指数
    state_rule: str = 'rule_default'        # 可交易域规则, 见 data.Panel
    universe_index: Optional[str] = None    # 限定成分股, None=全市场
    start: Optional[str] = None             # 该策略的回测起点, None = config.START
    params: Params = field(default_factory=Params)


# 口径原则: 条件②③④(宽度) 的股票池必须和条件①(趋势)的指数是同一批标的。
#
# 研报的美股部分就是这么做的 —— 趋势看道指/纽交所均线, 宽度明确写的是
# "纽交所个股新高与新低"、"纽交所创新高股票数", 同一个交易所。A股适配版
# 研报没写明池子, 但按它自己的逻辑, 显然该跟着指数走。
#
# 三种限定方式, 按指数性质选:
#   全市场      中证全指覆盖沪深A股(不含北交所), 剔掉 BJ 后的全市场就是它本身
#   交易所限定  上证综指 = 沪市全部A股, 没有固定成分名单, 用 include_exchanges
#   成分股限定  300/500/1000 有明确成分名单, 用 universe_index 走 constituents()

ASHARE = Strategy(
    name='中证全指',
    benchmark='000985.CSI',
    # 五个策略统一 rule_default。此前这一个用 rule_strict(额外剔 ST/*ST) 没有
    # 依据 —— 研报没要求剔 ST, 而被剔的 ST 股恰恰是创 52 周新低的主力, 会
    # 系统性压低 low_ratio, 让条件②更难满足。口径不一致还会让各策略的命中率
    # 失去可比性。要看剔不剔 ST 的影响, 跑 `python hindenburg.py compare`。
    state_rule='rule_default',
    # 中证全指基日 2004-12-31(点位=1000), 发布日才是 2011-08-02。
    # 数据商普遍把指数回填到基日, 所以起点按基日取而不是发布日 ——
    # 之前卡在 2011-08 是我们自己没去请求更早的数据, 不是数据不存在。
    # 若聚宽确实没有回填, IndexStore 会记 head_floor 并自动退到可得的最早日期。
    start='2005-01-01',
    # 不限定成分: 中证全指本就覆盖几乎全部沪深A股, 用剔掉北交所的全市场
    # 即可, 还能避开成分股名单的历史准确性问题。
    params=Params(),
)

GENERAL = Strategy(
    name='上证综指',
    benchmark='000001.SH',
    state_rule='rule_default',
    # 上证综指 1990 年至今都有, 状态表也从 2000-01-04 起, 所以能覆盖研报的
    # 2000-2024 全区间。研报的 A 股回测用的正是上证综指。
    start='2000-01-01',
    # 上证综指只含沪市股票(约2300只), 而全市场有5200只 —— 不限定的话
    # 56% 的宽度来自深市, 那些个股根本不在这个指数里, 条件①和条件②③④
    # 说的不是同一个市场。
    params=Params(include_exchanges=('SH',)),
)

# 三个规模指数 —— 趋势和宽度都限定在各自成分股内, 口径自洽。
# 起点用各指数官方发布日: 聚宽不会倒填发布日之前的数据(参考 000985.CSI
# 本地覆盖就是精确从 2011-08-02 开始, 早于发布日的请求会被截断)。
#
# ⚠ 阈值需要重新标定: 研报的 2.2% 是对着几千只标的的纽交所定的。沪深300
#   只有300只成分股, 2.2% = 6.6 只, 新高新低占比会非常跳跃, 条件②的统计
#   性质和全市场口径完全不同。这三个策略的结果只适合看"相对自己的历史",
#   不要直接和中证全指版比命中率。
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
# 运行时设置 (可被环境变量覆盖, 便于临时改而不动代码)
# =====================================================================

import os  # noqa: E402

DATA_DIR = os.getenv('HO_DATA_DIR', 'data')
START = os.getenv('HO_START', '2000-01-01')   # 策略未指定 start 时的默认起点
END = os.getenv('HO_END') or None          # None = 取本地价格面板最后一个交易日
CACHE_DIR = os.getenv('HO_CACHE_DIR', '.cache')
INDEX_DIR = os.getenv('HO_INDEX_DIR', 'index_store')
OUT_DIR = os.getenv('HO_OUT_DIR', 'output')     # clusters_*.csv / export_*.json / *.html
OFFLINE = os.getenv('HO_OFFLINE', '').lower() in ('1', 'true', 'yes')  # 强制纯本地

# 聚宽额度保护
JQ_RESERVE = int(os.getenv('JQ_RESERVE', 50_000))        # 给他人预留的额度下限(行)
JQ_MAX_ROWS = int(os.getenv('JQ_MAX_ROWS', 20_000))      # 单次指数更新自身上限
JQ_MAX_ROWS_PANEL = int(os.getenv('JQ_MAX_ROWS_PANEL', 500_000))  # 面板更新上限
JQ_DRY_RUN = os.getenv('JQ_DRY_RUN', '') == '1'
