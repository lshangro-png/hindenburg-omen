/* ============================================================
   i18n.js —— 页面文字的中英对照表，挂在 window.I18N 上

     I18N.LANG          当前语言 'zh' | 'en'（由页面里的 window.__LANG__ 指定）
     I18N.t(key, ...)   取界面文字；值是函数时把参数传进去
     I18N.v(value)      翻译数据里的中文取值（集群类型、回撤等级、指数名等），
                        中文页面原样返回
     I18N.apply()       填充 HTML 里带 data-i18n / data-v 属性的元素

   新增界面文字时，zh 和 en 两边都要加同一个 key。
   ============================================================ */
window.I18N = (function () {
  const LANG = window.__LANG__ === 'en' ? 'en' : 'zh';

  // 英文单复数：pl(1, 'signal') → '1 signal'，pl(3, 'signal') → '3 signals'
  const pl = (n, word) => `${n} ${word}${Number(n) === 1 ? '' : 's'}`;

  const DICT = {
    zh: {
      // ---------- 通用 ----------
      lang_switch: 'English', colon: '：',
      num1: '一', num2: '二', num3: '三', num4: '四', num5: '五', num6: '六',
      none: '无',
      pending_dd: '观察中',
      col_cluster: '集群', col_period: '预警区间', col_count: '特征数', col_type: '类型',
      col_dd: '回撤', col_drop: '跌幅', col_grade: '等级',
      index: '指数', threshold: '阈值', zero_axis: '0轴',
      new_high_pct: '新高占比', new_low_pct: '新低占比', new_high_n: '新高家数',
      fwd: (w) => `未来${w}日`,
      // ---------- 图表引擎 ----------
      ce_reset: '重置视图', ce_hint: '滚轮缩放 · 拖拽平移',
      ce_max: '最大', ce_median: '中位', ce_min: '最小',
      // ---------- 标注点 ----------
      mk_cluster: (id, lvl) => `集群#${id} · ${lvl}`,
      mk_single: (lvl) => `单点信号 · ${lvl}`,
      mk_range: (s, e, n) => `${s} ~ ${e} · ${n}次信号`,

      // ---------- 仪表盘 ----------
      dash_title: '兴登堡预兆 · 回测仪表盘',
      nav_summary: '核心结论', nav_trend: '指数走势', nav_panel: '四条件面板',
      nav_history: '信号回溯', nav_stats: '有效性统计', nav_compare: '策略对比',
      hero_dash: '兴登堡预兆<br>回测<em>仪表盘</em>',
      sample_period: '样本区间',
      sec_status: '当前状态', sec_status_sub: '现状快照，不是回测统计',
      sec_summary: '核心结论', sec_summary_sub: '集群信号后续回撤等级分布',
      sec_trend_sub: '按后续回撤分级着色；三角=有效集群，圆点=单点信号',
      sec_panel: '四条件监控面板',
      sec_history: '信号历史回溯表',
      sec_stats: '集群有效性统计',
      sec_compare: '策略对比',
      compare_sub: (tol) => `泳道图对比各策略有效集群的时间分布，虚线连接相邻泳道中时间重叠的集群（容忍 ±${tol} 个交易日）。
  各策略回测区间不同（见下表），且<b>回撤是各自基准指数的回撤</b>，命中率不能跨策略直接比大小。`,
      cmp_metric: '指标', cmp_period: '回测区间', cmp_bench: '趋势基准', cmp_nvalid: '有效集群数',
      cmp_hit_mid: '中型命中率', cmp_base_mid: '中型基准率', cmp_hit_big: '较大命中率', cmp_base_big: '较大基准率',
      foot: (dd) => `<b>局限</b>：可评估样本量小，命中率置信区间宽；回撤口径为${dd}；不构成投资建议。`,
      caption: (s, src, w) => `${s} · ${src} · 集群窗口${w}日`,
      range: (a, b, n) => `${a} ~ ${b}（${n} 个交易日）`,
      dd_peak: '窗口内峰谷最大回撤', dd_from: '锚点日收盘起最低点跌幅',
      anchor: (confirm) => `（起算点：${confirm ? '集群确认日' : '集群首日'}）`,
      st_last: '最近信号日', st_asof: (d) => `截至 ${d}`,
      st_recent: (w) => `近 ${w} 日单点信号`, st_recent_foot: '不要求同属一个集群',
      st_cluster: '近期集群',
      st_cluster_foot: (s, e, n) => `${s} ~ ${e}，${n} 次信号`,
      st_confirmed: (d) => `，已于 ${d} 确认`, st_unconfirmed: '，尚未确认',
      st_no_cluster: (w) => `近 ${w} 日内无集群`,
      sc_total: '集群总数', sc_total_foot: (n) => `含 ${n} 个近期信号`,
      sc_mid: '中型以上回撤命中', sc_big: '较大回撤命中', sc_win: (gw, c) => `${gw} 日 ≤ ${c}%`,
      sc_rate: '中型回撤命中率', sc_rate_foot: (n) => `基于 ${n} 个可评估集群`,
      summary_note: (n, hit, base) => `<b>命中率要和基准率对照着看。</b>当前可评估集群 ${n} 个，中型回撤命中率 ${hit}；` +
        `而随便挑一天，未来一年出现同等回撤的概率（基准率）就有 ${base}。` +
        `在 A 股上，这个指标没有表现出稳定的预警能力，加上样本量很小，不宜据此做交易决策。`,
      trend_title: (src) => `${src} 走势与信号标注`,
      trend_sub: '可滚轮缩放/拖拽 · 三角=有效集群 圆点=单点信号',
      c1_title: '条件① 趋势向上', c1_sub: (src, n) => `${src} vs ${n}日均线`,
      c2_title: '条件② 新高/新低占比', c2_sub: (x) => `阈值 ${x}%`,
      c3_title: (r) => `条件③ 新高 ≤ 新低×${r}`, c3_sub: '柱=新高家数',
      c4_title: '条件④ McClellan振荡器', c4_sub: (a, b) => `EMA${a}-EMA${b}，0轴之下=满足`,
      panel_sub: (r1, r2, r3, r4, k, n) =>
        `条件① ${r1} · 条件② ${r2} · 条件③ ${r3} · 条件④ ${r4} · 四条件同时满足 ${k} 天 / ${n} 天`,
      dd_head: (gw) => `${gw}日回撤`,
      history_sub: (gw, peak) => `"特征数"为集群内信号触发次数；"${gw}日回撤"为锚点日起未来 ${gw} 个交易日的` +
        (peak ? '峰谷最大回撤' : '最低点跌幅'),
      stats_sub: (n) => `对有效集群锚点日起未来各窗口最大回撤的分布, 基于 ${n} 个观察期已满的集群`,
      box_title: '集群信号后续最大回撤分布', box_sub: (n) => `基于 ${n} 个集群`,
      tl_title: '各策略有效集群时间分布', tl_sub: '虚线=判定为同一事件的匹配',

      // ---------- 参数实验室 ----------
      lab_title: '兴登堡预兆 · 参数实验室',
      lab_back: '← 回测仪表盘',
      lab_eyebrow: '实时重算 · 无需重跑数据',
      hero_lab: '参数<em>实验室</em>',
      lab_intro: `拖动左侧滑块，四条件判定、集群划分、回撤评级会立刻重算——因为导出的数据里带着
  指数收盘、新高新低占比、家数、McClellan 值这些<b>中间序列</b>，四个条件都能由它们推出来。
  计算逻辑与 <code>backtest.py</code> 逐函数对应（已验证结果完全一致）。`,
      light_params: '轻参数', light_hint: '这些只依赖已算好的中间序列，改动毫秒级生效。',
      cluster_mode: '集群方式', opt_fixed: 'fixed · 窗口从首日固定', opt_chain: 'chain · 链式延展',
      dd_mode: '回撤口径', opt_peak: 'peak_trough · 窗口内峰谷', opt_from: 'from_signal · 锚点日起最低点',
      reset: '恢复默认',
      heavy_title: '重参数（改这些必须重跑 Python）',
      heavy_note: `这些参数决定了 McClellan 值、新高新低占比这些<b>输入序列本身</b>。
      浏览器里没有全市场股票面板，算不出来。要改就在 <code>config.py</code> 里改，然后重跑
      <code>python hindenburg.py clear-cache &amp;&amp; python hindenburg.py export</code>，
      再 <code>python lab.py</code> 重新生成本页。`,
      lab_foot1: `<b>如何用</b>：先在这里把轻参数调顺（免费、实时），定下来之后再去 <code>config.py</code>
  改重参数并重跑。左上角"恢复默认"回到 config.py 里的设定值。`,
      lab_foot2: `<b>注意</b>：调参极易过拟合。样本量本就很小，多试几组总能找到"看起来很准"的组合，
  但那多半是过拟合。`,
      sl_ma_period: '① 趋势均线周期', sl_ma_lookback: '① 均线比较滞后', sl_threshold: '② 新高/新低占比阈值',
      sl_hl_ratio: '③ 新高 ≤ 新低 ×', sl_cluster_window: '集群确认窗口', sl_cluster_min: '升级为集群的最少信号数',
      u_ma_period: '日', u_ma_lookback: '日', u_threshold: '', u_hl_ratio: '倍', u_cluster_window: '日', u_cluster_min: '次',
      sl_default: (x) => `默认 ${x}`,
      ls_signal_days: '信号天数', ls_valid: '有效集群', ls_gradable: '可评估', ls_hit: '命中率',
      ls_hit_mid: (gw, c) => `中型以上命中(${gw}日≤${c}%)`, ls_hit_big: (c) => `较大回撤命中(≤${c}%)`,
      ls_base: '同期基准率(全样本随机一天)', ls_excess: '超额(命中率 - 基准率)',
      lab_few: (n) => `<b>样本过少</b>：当前参数下只剩 ${n} 个可评估集群，命中率已经没有统计意义了。`,
      lab_now: (n, hit, base, ex) => `<b>当前参数</b>：${n} 个可评估集群，中型回撤命中率 ${hit}，` +
        `同期基准率 ${base}，超额 ${ex}。` +
        `样本量本就很小，命中率和基准率的差多半是噪声，不要只盯着命中率数字调参——` +
        `绿色/红色小字是相对 config.py 默认参数的变化。`,
      no_index: '指数无数据',
      lab_trend_title: (src) => `${src} 走势与当前参数下的信号`, lab_trend_sub: '可滚轮缩放/拖拽',
      lab_c1: (n) => `条件① MA${n} 上升`, holds: (x) => `触发率 ${x}`,
      lab_c2: (x) => `条件② 占比 ≥ ${x}%`, lab_c3: (r) => `条件③ 新高 ≤ 新低×${r}`,
      lab_c4: '条件④ McClellan < 0', lab_c4_sub: (x) => `触发率 ${x} · 该条件由重参数决定`,
      lab_box_title: '当前参数下的跌幅分布', lab_box_sub: (n) => `基于 ${n} 个可评估集群`,
      lab_no_signal: '当前参数下没有任何信号',
    },

    en: {
      // ---------- common ----------
      lang_switch: '中文', colon: ': ',
      num1: '1', num2: '2', num3: '3', num4: '4', num5: '5', num6: '6',
      none: 'None',
      pending_dd: 'pending',
      col_cluster: 'Cluster', col_period: 'Warning period', col_count: 'Signals', col_type: 'Type',
      col_dd: 'Drawdown', col_drop: 'Drawdown', col_grade: 'Grade',
      index: 'Index', threshold: 'Threshold', zero_axis: 'Zero',
      new_high_pct: 'New highs %', new_low_pct: 'New lows %', new_high_n: 'New highs',
      fwd: (w) => `Next ${w}d`,
      // ---------- chart engine ----------
      ce_reset: 'Reset view', ce_hint: 'Scroll to zoom · drag to pan',
      ce_max: 'Max', ce_median: 'Median', ce_min: 'Min',
      // ---------- markers ----------
      mk_cluster: (id, lvl) => `Cluster #${id} · ${lvl}`,
      mk_single: (lvl) => `Single signal · ${lvl}`,
      mk_range: (s, e, n) => `${s} ~ ${e} · ${pl(n, 'signal')}`,

      // ---------- dashboard ----------
      dash_title: 'Hindenburg Omen · Backtest Dashboard',
      nav_summary: 'Key results', nav_trend: 'Index', nav_panel: 'Conditions',
      nav_history: 'History', nav_stats: 'Effectiveness', nav_compare: 'Comparison',
      hero_dash: 'Hindenburg Omen<br>Backtest <em>Dashboard</em>',
      sample_period: 'Sample period',
      sec_status: 'Current status', sec_status_sub: 'A snapshot of where things stand now, not backtest statistics',
      sec_summary: 'Key results', sec_summary_sub: 'Drawdown grades following cluster signals',
      sec_trend_sub: 'Colored by subsequent drawdown grade; triangle = valid cluster, dot = single signal',
      sec_panel: 'Four-condition monitor',
      sec_history: 'Signal history',
      sec_stats: 'Cluster effectiveness',
      sec_compare: 'Strategy comparison',
      compare_sub: (tol) => `The swim-lane chart shows when each strategy's valid clusters occurred; dashed lines connect
  clusters in adjacent lanes that overlap in time (within ±${tol} trading days). Backtest periods differ across
  strategies (see the table), and <b>each drawdown is measured on that strategy's own benchmark index</b>, so hit rates
  cannot be compared directly across strategies.`,
      cmp_metric: 'Metric', cmp_period: 'Backtest period', cmp_bench: 'Trend benchmark', cmp_nvalid: 'Valid clusters',
      cmp_hit_mid: 'Hit rate (moderate)', cmp_base_mid: 'Base rate (moderate)',
      cmp_hit_big: 'Hit rate (major)', cmp_base_big: 'Base rate (major)',
      foot: (dd) => `<b>Limitations</b>: few gradable samples, so hit-rate confidence intervals are wide; drawdowns are measured as ${dd}; not investment advice.`,
      caption: (s, src, w) => `${s} · ${src} · cluster window ${w}d`,
      range: (a, b, n) => `${a} ~ ${b} (${n} trading days)`,
      dd_peak: 'the peak-to-trough maximum drawdown within the window', dd_from: 'the maximum decline from the anchor-day close',
      anchor: (confirm) => ` (measured from the ${confirm ? 'cluster confirmation date' : 'first day of the cluster'})`,
      st_last: 'Last signal date', st_asof: (d) => `As of ${d}`,
      st_recent: (w) => `Signals in the last ${w} days`, st_recent_foot: 'Not necessarily from one cluster',
      st_cluster: 'Recent cluster',
      st_cluster_foot: (s, e, n) => `${s} ~ ${e}, ${pl(n, 'signal')}`,
      st_confirmed: (d) => `, confirmed on ${d}`, st_unconfirmed: ', not yet confirmed',
      st_no_cluster: (w) => `No cluster in the last ${w} days`,
      sc_total: 'Valid clusters', sc_total_foot: (n) => `incl. ${n} pending`,
      sc_mid: 'Moderate+ drawdown hits', sc_big: 'Major drawdown hits', sc_win: (gw, c) => `${gw}d ≤ ${c}%`,
      sc_rate: 'Moderate hit rate', sc_rate_foot: (n) => `Based on ${pl(n, 'gradable cluster')}`,
      summary_note: (n, hit, base) => `<b>Read the hit rate against the base rate.</b> There are currently ${pl(n, 'gradable cluster')},
  with a moderate-drawdown hit rate of ${hit}; yet for any randomly chosen day, the chance of an equally large drawdown within a year
  (the base rate) is already ${base}. On A-shares this indicator has shown no consistent predictive power, and with a sample this small
  it should not be used to make trading decisions.`,
      trend_title: (src) => `${src} with signals`,
      trend_sub: 'Scroll to zoom / drag · triangle = valid cluster, dot = single signal',
      c1_title: '① Uptrend', c1_sub: (src, n) => `${src} vs ${n}-day MA`,
      c2_title: '② Share at new highs / new lows', c2_sub: (x) => `Threshold ${x}%`,
      c3_title: (r) => `③ New highs ≤ new lows × ${r}`, c3_sub: 'Bars = number of new highs',
      c4_title: '④ McClellan Oscillator', c4_sub: (a, b) => `EMA${a} − EMA${b}; below zero = condition met`,
      panel_sub: (r1, r2, r3, r4, k, n) =>
        `① ${r1} · ② ${r2} · ③ ${r3} · ④ ${r4} · all four together on ${k} of ${n} days`,
      dd_head: (gw) => `${gw}d drawdown`,
      history_sub: (gw, peak) => `"Signals" is the number of signals in the cluster; "${gw}d drawdown" is the ` +
        (peak ? 'peak-to-trough maximum drawdown' : 'maximum decline') + ` over the ${gw} trading days after the anchor date`,
      stats_sub: (n) => `Distribution of maximum drawdowns after the anchor date of valid clusters, based on ${pl(n, 'cluster')} with a full observation window`,
      box_title: 'Maximum drawdown after cluster signals', box_sub: (n) => `Based on ${pl(n, 'cluster')}`,
      tl_title: 'Valid clusters over time, by strategy', tl_sub: 'Dashed line = matched as the same event',

      // ---------- parameter lab ----------
      lab_title: 'Hindenburg Omen · Parameter Lab',
      lab_back: '← Backtest dashboard',
      lab_eyebrow: 'Live recalculation · no re-run needed',
      hero_lab: 'Parameter <em>Lab</em>',
      lab_intro: `Drag the sliders on the left and the four conditions, clusters and drawdown grades recompute instantly.
  The exported data carries the <b>intermediate series</b> (index close, share of new highs/lows, counts, McClellan values),
  from which all four conditions can be derived. The logic mirrors <code>backtest.py</code> function by function
  (results verified to match exactly).`,
      light_params: 'Light parameters', light_hint: 'These only use precomputed series, so changes take effect in milliseconds.',
      cluster_mode: 'Clustering', opt_fixed: 'fixed · window fixed from first signal', opt_chain: 'chain · extends with each signal',
      dd_mode: 'Drawdown', opt_peak: 'peak_trough · peak-to-trough', opt_from: 'from_signal · from anchor close',
      reset: 'Reset to defaults',
      heavy_title: 'Heavy parameters (changing these requires re-running Python)',
      heavy_note: `These parameters determine the <b>input series themselves</b> (McClellan values, shares of new highs/lows).
      The browser does not have the full stock panel, so it cannot compute them. To change them, edit <code>config.py</code>, run
      <code>python hindenburg.py clear-cache &amp;&amp; python hindenburg.py export</code>,
      then <code>python lab.py</code> to rebuild this page.`,
      lab_foot1: `<b>How to use</b>: tune the light parameters here first (free and instant); once settled, change heavy
  parameters in <code>config.py</code> and re-run. "Reset to defaults" returns to the values in config.py.`,
      lab_foot2: `<b>Caution</b>: tuning overfits very easily. With samples this small, trying enough combinations will always
  turn up one that "looks accurate", but it is most likely overfitted.`,
      sl_ma_period: '① Trend MA period', sl_ma_lookback: '① MA comparison lag', sl_threshold: '② New high/low share threshold',
      sl_hl_ratio: '③ New highs ≤ new lows ×', sl_cluster_window: 'Cluster window', sl_cluster_min: 'Min signals for a cluster',
      u_ma_period: 'd', u_ma_lookback: 'd', u_threshold: '', u_hl_ratio: '×', u_cluster_window: 'd', u_cluster_min: '',
      sl_default: (x) => `default ${x}`,
      ls_signal_days: 'Signal days', ls_valid: 'Valid clusters', ls_gradable: 'Gradable', ls_hit: 'Hit rate',
      ls_hit_mid: (gw, c) => `Moderate+ hits (${gw}d ≤ ${c}%)`, ls_hit_big: (c) => `Major hits (≤ ${c}%)`,
      ls_base: 'Base rate (random day)', ls_excess: 'Excess (hit rate − base rate)',
      lab_few: (n) => `<b>Too few samples</b>: only ${pl(n, 'gradable cluster')} ${n === 1 ? 'remains' : 'remain'} with these parameters; the hit rate is no longer statistically meaningful.`,
      lab_now: (n, hit, base, ex) => `<b>Current parameters</b>: ${pl(n, 'gradable cluster')}, moderate hit rate ${hit}, ` +
        `base rate ${base}, excess ${ex}. ` +
        `The sample is small, so the gap between hit rate and base rate is mostly noise; don't tune just to push the hit rate up. ` +
        `The small green/red numbers show the change relative to the defaults in config.py.`,
      no_index: 'No index data',
      lab_trend_title: (src) => `${src} with signals under the current parameters`, lab_trend_sub: 'Scroll to zoom / drag',
      lab_c1: (n) => `① MA${n} rising`, holds: (x) => `Holds ${x}`,
      lab_c2: (x) => `② Shares ≥ ${x}%`, lab_c3: (r) => `③ New highs ≤ new lows × ${r}`,
      lab_c4: '④ McClellan < 0', lab_c4_sub: (x) => `Holds ${x} · set by heavy parameters`,
      lab_box_title: 'Drawdown distribution under the current parameters', lab_box_sub: (n) => `Based on ${pl(n, 'gradable cluster')}`,
      lab_no_signal: 'No signals with the current parameters',
    },
  };

  // 数据里的中文取值 → 英文显示
  const VALUES_EN = {
    '有效集群': 'Cluster', '单点': 'Single',
    '较大回撤': 'Major drawdown', '中型回调': 'Moderate pullback', '小幅回调': 'Minor pullback',
    '无显著回撤': 'No drawdown', '近期信号': 'Pending',
    '中证全指': 'CSI All Share', '上证综指': 'SSE Composite', '沪深300': 'CSI 300',
    '中证500': 'CSI 500', '中证1000': 'CSI 1000',
    '中证全指(不复权+不过滤·实验对照)': 'CSI All Share (unadjusted, unfiltered · experimental)',
    '不过滤(实验组)': 'unfiltered (experimental)',
    '等权代理': 'Equal-weight proxy',
  };

  const T = DICT[LANG];

  function t(key, ...args) {
    const val = T[key];
    if (val === undefined) { console.warn('[i18n] missing key', key); return key; }
    return typeof val === 'function' ? val(...args) : val;
  }

  function v(s) {
    return LANG === 'en' && s in VALUES_EN ? VALUES_EN[s] : s;
  }

  function apply(root) {
    root = root || document;
    root.querySelectorAll('[data-i18n]').forEach(el => {
      el.innerHTML = t(el.dataset.i18n, ...(el.dataset.arg !== undefined ? [el.dataset.arg] : []));
    });
    root.querySelectorAll('[data-v]').forEach(el => { el.textContent = v(el.dataset.v); });
  }

  return {LANG, t, v, apply};
})();
