/* ============================================================
   backtest.js —— 回测核心的 JS 镜像

   逐行对应 02_回测.ipynb 里的 build_signals / build_clusters /
   evaluate / summarize。参数实验室靠它在浏览器里
   重算轻参数, 不需要重跑 Python。

   能重算的只有轻参数(见 02_回测.ipynb 的 LIGHT_KEYS): 四条件判定用的
   阈值、集群窗口、评级分界。重参数(EMA周期/52周窗口/可交易域规则)
   决定的是 mcclellan、high_ratio 这些输入序列本身, 浏览器里没有
   全市场股票面板, 算不出来, 必须重跑 Python。
   ============================================================ */

window.BT = (() => {

  function rollingMean(arr, window) {
    // 对应 pandas rolling(window).mean(): 窗口内有任一 null 则该点为 null
    const out = new Array(arr.length).fill(null);
    let sum = 0, cnt = 0;
    const buf = [];
    for (let i = 0; i < arr.length; i++) {
      const v = arr[i];
      buf.push(v);
      if (v !== null && v !== undefined && !Number.isNaN(v)) { sum += v; cnt++; }
      if (buf.length > window) {
        const old = buf.shift();
        if (old !== null && old !== undefined && !Number.isNaN(old)) { sum -= old; cnt--; }
      }
      if (buf.length === window && cnt === window) out[i] = sum / window;
    }
    return out;
  }

  /**
   * 对应 build_signals。
   * pre 是回测起点之前的指数收盘(export 里的 index_pre), 用来给条件① 的均线
   * 预热。不带它的话头 ma_period + ma_lookback 天 cond1 恒 false, 与 Python 不符。
   */
  function buildSignals(series, p, pre) {
    const n = series.dates.length;
    const preClose = (pre && pre.index_close) ? pre.index_close : [];
    const k = preClose.length;
    const maFull = rollingMean(preClose.concat(series.index_close), p.ma_period);
    const ma = maFull.slice(k);          // 裁回回测区间
    const cond1 = new Array(n).fill(false);
    const cond2 = new Array(n).fill(false);
    const cond3 = new Array(n).fill(false);
    const cond4 = new Array(n).fill(false);
    const signal = new Array(n).fill(false);
    for (let i = 0; i < n; i++) {
      // 滞后期可以落到预热段里, 所以在 maFull 上取而不是在裁过的 ma 上取
      const cur = maFull[i + k];
      let c1 = false;
      if (p.trend_mode === 'ma_rising') {
        // 美股原始定义: 均线本身在上升, MA(t) > MA(t-ma_lookback)
        const jf = i + k - p.ma_lookback;
        const prev = jf >= 0 ? maFull[jf] : null;
        c1 = (cur !== null && prev !== null) ? cur > prev : false;
      } else if (p.trend_mode === 'above_ma') {
        // 浙商 A股适配版口径: 价格站在均线上方, Close(t) > MA(t)
        const px = series.index_close[i];
        c1 = (px !== null && cur !== null) ? px > cur : false;
      } else {
        throw new Error(`trend_mode 只能是 ma_rising 或 above_ma, 收到 ${p.trend_mode}`);
      }
      cond1[i] = c1;
      const hr = series.high_ratio[i], lr = series.low_ratio[i];
      cond2[i] = (hr !== null && lr !== null) ? (hr >= p.threshold && lr >= p.threshold) : false;
      const nh = series.new_high[i], nl = series.new_low[i];
      cond3[i] = (nh !== null && nl !== null) ? (nh <= nl * p.hl_ratio) : false;
      const mc = series.mcclellan[i];
      cond4[i] = (mc !== null) ? (mc < 0) : false;
      signal[i] = cond1[i] && cond2[i] && cond3[i] && cond4[i];
    }
    return { cond1, cond2, cond3, cond4, signal, ma };
  }

  /**
   * 对应 build_clusters, 两种模式:
   *   fixed 窗口从集群首日固定, 集群跨度上限 = cluster_window
   *   chain 链式延展, 每个新信号把窗口重新往后延, 跨度可远超 cluster_window
   */
  function buildClusters(dates, signal, p) {
    const idxs = [];
    for (let i = 0; i < signal.length; i++) if (signal[i]) idxs.push(i);
    if (!idxs.length) return [];
    const groups = [];

    if (p.cluster_mode === 'chain') {
      let g = [idxs[0]];
      for (let k = 1; k < idxs.length; k++) {
        if (idxs[k] - idxs[k - 1] <= p.cluster_window) g.push(idxs[k]);
        else { groups.push(g); g = [idxs[k]]; }
      }
      groups.push(g);
    } else {
      const used = new Set();
      for (const d of idxs) {
        if (used.has(d)) continue;
        const endIdx = Math.min(d + p.cluster_window, signal.length - 1);
        const members = idxs.filter(x => x >= d && x <= endIdx && !used.has(x));
        members.forEach(x => used.add(x));
        groups.push(members);
      }
    }

    return groups.map((g, i) => {
      // 确认日 = 第 cluster_min 个信号那天。集群"是不是有效集群"要看到后续信号
      // 才知道, 从首日起算回撤等于用了未来信息。
      const hasConfirm = g.length >= p.cluster_min;
      const ic = hasConfirm ? g[p.cluster_min - 1] : null;
      return {
        id: i + 1, i0: g[0], i1: g[g.length - 1], iConfirm: ic,
        start: dates[g[0]], end: dates[g[g.length - 1]],
        confirm: ic === null ? null : dates[ic],
        count: g.length,
        type: hasConfirm ? '有效集群' : '单点',
      };
    });
  }

  /** 对应 grade */
  function gradeOf(dd, cuts) {
    if (dd === null || dd === undefined || Number.isNaN(dd)) return '近期信号';
    const [big, mid, small] = cuts;
    return dd <= big ? '较大回撤' : dd <= mid ? '中型回调'
         : dd <= small ? '小幅回调' : '无显著回撤';
  }

  /**
   * 对应 evaluate。两种回撤口径:
   *   from_signal  相对信号日收盘价的最低点跌幅
   *   peak_trough  窗口内峰谷最大回撤(允许先涨到高点再从高点起算)
   * 牛市里两者差别极大: 指数一路上涨再崩盘时, from_signal 可能接近 0,
   * peak_trough 却是几十个点。
   */
  function drawdown(close, s, w, mode) {
    const n = close.length;
    if (s + w >= n) return null;
    if (mode === 'peak_trough') {
      let peak = -Infinity, worst = 0, any = false;
      for (let i = s; i <= s + w; i++) {
        const v = close[i];
        if (v === null || v === undefined || Number.isNaN(v)) continue;
        any = true;
        if (v > peak) peak = v;
        const dd = v / peak - 1;
        if (dd < worst) worst = dd;
      }
      return any ? worst : null;
    }
    let min = Infinity;
    for (let i = s; i <= s + w; i++) {
      const v = close[i];
      if (v !== null && v !== undefined && !Number.isNaN(v) && v < min) min = v;
    }
    const base = close[s];
    return (base === null || !isFinite(min)) ? null : min / base - 1;
  }

  function evaluate(close, clusters, p) {
    for (const c of clusters) {
      // dd_anchor: 'confirm' 用确认日(无前视), 'first' 用首日(研报口径)
      const iA = (p.dd_anchor === 'first' || c.iConfirm === null) ? c.i0 : c.iConfirm;
      c.iAnchor = iA;
      c.anchor = c.iAnchor;
      for (const w of p.dd_windows) c['dd' + w] = drawdown(close, iA, w, p.dd_mode);
      c.level = gradeOf(c['dd' + p.grade_window], p.grade_cuts);
    }
    return clusters;
  }

  /**
   * 对应 signal_day_levels —— 走势图用的逐日标注, 不是回测统计。
   * 每个原始信号日都标一个点(颜色用它自己往后 grade_window 天的回撤),
   * 属于某个有效集群的信号日额外带上 cluster_id, 前端据此换成三角形。
   * 不需要 dd_anchor 校正: 每个点用的就是它自己的日期, 没有前视问题。
   */
  function signalDayLevels(dates, close, signal, clusters, p) {
    const ranges = clusters.filter(c => c.type === '有效集群')
      .map(c => ({ lo: c.i0, hi: c.i1, id: c.id }));
    const clusterOf = i => {
      for (const r of ranges) if (i >= r.lo && i <= r.hi) return r.id;
      return null;
    };
    const out = [];
    signal.forEach((s, i) => {
      if (!s) return;
      const dd = drawdown(close, i, p.grade_window, p.dd_mode);
      out.push({ date: dates[i], index: i, level: gradeOf(dd, p.grade_cuts),
                 cluster_id: clusterOf(i) });
    });
    return out;
  }

  /**
   * 对应 baseline_rates —— 无条件对照组。
   * 把每一天都当信号日算同口径回撤。没有它, 命中率读不出意思:
   * A股 252 日窗口内出现 >=10% 峰谷回撤的无条件概率约 97%。
   * mask 传 cond1 就得到"仅趋势向上时"的基准。
   */
  function baselineRates(close, p, mask) {
    const n = close.length, out = {};
    const [big, mid, small] = p.grade_cuts;
    for (const w of p.dd_windows) {
      const vals = [];
      for (let s = 0; s + w < n; s++) {
        if (mask && !mask[s]) continue;
        const v = drawdown(close, s, w, p.dd_mode);
        if (v !== null && !Number.isNaN(v)) vals.push(v);
      }
      if (!vals.length) continue;
      const sorted = [...vals].sort((a, b) => a - b);
      out[w] = {
        n: vals.length, median: quantile(sorted, .5),
        p_small: vals.filter(v => v <= small).length / vals.length,
        p_mid: vals.filter(v => v <= mid).length / vals.length,
        p_big: vals.filter(v => v <= big).length / vals.length,
      };
    }
    return out;
  }

  function quantile(sorted, q) {
    if (!sorted.length) return null;
    const pos = (sorted.length - 1) * q;
    const lo = Math.floor(pos), hi = Math.ceil(pos);
    if (lo === hi) return sorted[lo];
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  }

  /**
   * 对应 current_status —— "此刻"状态快照, 不是回测统计。
   * 不涉及往后看, 只老实报告"过去 cluster_window 个交易日里原始信号出现了
   * 几次", 以及最近一个集群是否仍"新鲜"及其确认状态。
   */
  function currentStatus(dates, signal, clusters, p) {
    const n = dates.length;
    const out = { as_of: n ? dates[n - 1] : null, window: p.cluster_window,
                  last_signal_date: null, recent_signal_count: 0, active_cluster: null };
    if (!n) return out;

    const sigI = [];
    signal.forEach((s, i) => { if (s) sigI.push(i); });
    if (sigI.length) {
      out.last_signal_date = dates[sigI[sigI.length - 1]];
      const cutoff = n - 1 - p.cluster_window;
      out.recent_signal_count = sigI.filter(i => i > cutoff).length;
    }

    if (clusters.length) {
      const windowStartI = Math.max(0, n - 1 - p.cluster_window);
      const fresh = clusters.filter(c => c.i1 >= windowStartI);
      if (fresh.length) {
        const r = fresh.reduce((a, b) => (b.i1 > a.i1 ? b : a));
        out.active_cluster = {
          id: r.id, type: r.type, start: r.start, end: r.end, count: r.count,
          confirm_date: r.confirm,
        };
      }
    }
    return out;
  }

  /** 一次算完: 信号 -> 集群 -> 评级 -> 统计。对应 summarize */
  function runAll(series, p, pre) {
    const sig = buildSignals(series, p, pre);
    let clusters = buildClusters(series.dates, sig.signal, p);
    clusters = evaluate(series.index_close, clusters, p);

    const valid = clusters.filter(c => c.type === '有效集群');
    const gradable = valid.filter(c => c.level !== '近期信号');
    const ddKey = 'dd' + p.grade_window;
    const hitMid = gradable.filter(c => c[ddKey] <= p.grade_cuts[1]).length;
    const hitBig = gradable.filter(c => c[ddKey] <= p.grade_cuts[0]).length;

    const levelCounts = {};
    valid.forEach(c => { levelCounts[c.level] = (levelCounts[c.level] || 0) + 1; });

    const box = {};
    for (const w of p.dd_windows) {
      const vs = gradable.map(c => c['dd' + w]).filter(v => v !== null && !Number.isNaN(v));
      if (!vs.length) continue;
      const s = [...vs].sort((a, b) => a - b);
      box[w] = { min: s[0], q1: quantile(s, .25), median: quantile(s, .5),
                 q3: quantile(s, .75), max: s[s.length - 1], n: s.length };
    }

    const n = series.dates.length;
    const rate = k => sig[k].filter(Boolean).length / n;

    const baseAll = baselineRates(series.index_close, p, null);
    const baseUp = baselineRates(series.index_close, p, sig.cond1);
    const gw = p.grade_window;
    const hitRate = gradable.length ? hitMid / gradable.length : null;
    const hitRateBig = gradable.length ? hitBig / gradable.length : null;
    const excessOf = (rate, key) => (rate !== null && baseAll[gw]) ? {
      vs_all: rate - baseAll[gw][key],
      vs_uptrend: baseUp[gw] ? rate - baseUp[gw][key] : null,
    } : null;
    const excess = excessOf(hitRate, 'p_mid');
    const excessBig = excessOf(hitRateBig, 'p_big');

    // 各窗口(周/月/季/年)各自的命中率与超额, 分母是该窗口自己 notna 的集群数
    const windowHits = {};
    for (const w of p.dd_windows) {
      const key = 'dd' + w;
      const sub = valid.filter(c => c[key] !== null && !Number.isNaN(c[key]));
      const nw = sub.length;
      if (!nw) continue;
      const hm = sub.filter(c => c[key] <= p.grade_cuts[1]).length;
      const hb = sub.filter(c => c[key] <= p.grade_cuts[0]).length;
      const ba = baseAll[w];
      windowHits[w] = {
        n: nw, hit_mid: hm, hit_big: hb, rate_mid: hm / nw, rate_big: hb / nw,
        excess_mid: ba ? (hm / nw - ba.p_mid) : null,
        excess_big: ba ? (hb / nw - ba.p_big) : null,
      };
    }

    const status = currentStatus(series.dates, sig.signal, clusters, p);
    const signalDays = signalDayLevels(series.dates, series.index_close, sig.signal, clusters, p);

    return {
      ...sig, clusters, valid, gradable, box, signalDays,
      baseline_all: baseAll, baseline_uptrend: baseUp, excess,
      meta: {
        n_days: n,
        signal_days: sig.signal.filter(Boolean).length,
        cond_rate: { cond1: rate('cond1'), cond2: rate('cond2'),
                     cond3: rate('cond3'), cond4: rate('cond4') },
        n_clusters_total: clusters.length,
        n_clusters_valid: valid.length,
        n_gradable: gradable.length,
        hit_mid: hitMid, hit_big: hitBig,
        hit_rate: hitRate, hit_rate_big: hitRateBig,
        baseline_all: baseAll, baseline_uptrend: baseUp,
        excess, excess_big: excessBig,
        window_hits: windowHits, status,
        level_counts: levelCounts,
      },
    };
  }

  // 只导出外部真正用到的两个:
  //   runAll       一次算完信号/集群/评级/统计, 参数实验室和仪表盘都靠它
  //   rollingMean  仪表盘画条件①的均线时单独用一次
  // 其余(buildSignals/buildClusters/evaluate/baselineRates/
  // currentStatus/signalDayLevels/drawdown/gradeOf)都是
  // runAll 的内部步骤, 结果已经在 runAll 的返回值里, 不需要再暴露一遍。
  // 要单独调它们做对拍测试的话, 在这里临时加回去即可。
  return { runAll, rollingMean };
})();
