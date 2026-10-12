"""
viz.py —— 把回测结果渲染成 HTML 仪表盘

产出 dashboard.html: 固定参数下的完整结果, 多策略切换 + 策略对比。

参数实验室(拖滑块实时重算轻参数)已经拆到 lab.py, 作为独立脚本单独运行,
不在这个模块里、也不在 `python hindenburg.py viz` 的默认产出里 —— 它只是
调参时的个人工具, 不是要交付的东西。用法见 lab.py 顶部说明。
"""
from __future__ import annotations

import json
from pathlib import Path

import config

ASSETS = Path(__file__).parent / 'assets'

# 回撤等级 -> 颜色。键必须和 hindenburg.grade() / backtest.js 的返回值一字不差
# (含 '近期信号'), 否则该等级的集群在徽标/图例里会被静默漏掉。
# 等级到 CSS 徽标类名的映射只在浏览器端用得到, 写在 DASHBOARD_JS 的 TAG 常量里。
LEVEL_COLOR = {'较大回撤': '#C64B37', '中型回调': '#E0904A', '小幅回调': '#D9A441',
               '无显著回撤': '#4FA98C', '近期信号': '#5C8AE6'}


def _pct(x, d=0):
    return '—' if x is None else f'{x*100:.{d}f}%'


def _asset(name: str) -> str:
    return (ASSETS / name).read_text(encoding='utf-8')


def _head(title: str, extra_css: str = '') -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@500;700&family=Noto+Sans+SC:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
{_asset('report.css')}
{extra_css}
</style>
</head>
<body>"""


# 仪表盘专用的额外样式(策略切换/对比表)。参数实验室专用的 .lab-* 样式
# 已经搬到 lab.py 的 LAB_CSS 里, 两边各自独立, 不共享同一个大字符串了。
DASHBOARD_CSS = """
.toggle-row{display:flex; align-items:center; gap:12px; margin:0 0 24px; flex-wrap:wrap;}
.toggle-group{display:inline-flex; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:4px;}
.toggle-btn{font-family:var(--font-body); font-size:13.5px; font-weight:500; color:var(--text-dim);
  background:none; border:none; padding:8px 18px; border-radius:7px; cursor:pointer; transition:.15s;}
.toggle-btn.active{background:var(--amber); color:#FFFFFF; font-weight:600;}
.toggle-btn:not(.active):hover{color:var(--text);}
.toggle-caption{font-size:12.5px; color:var(--text-faint); font-family:var(--font-mono);}
.cmp-tbl th, .cmp-tbl td{text-align:center;}
.cmp-tbl th:first-child, .cmp-tbl td:first-child{text-align:left;}
"""


# =====================================================================
# 仪表盘
# =====================================================================

def build_dashboard(payloads: dict, out_path: Path) -> Path:
    keys = list(payloads)
    first = payloads[keys[0]]
    meta = first['meta']

    btns = '\n'.join(
        f'<button class="toggle-btn{" active" if i == 0 else ""}" data-cfg="{k}" type="button">'
        f'{payloads[k]["strategy"]}</button>' for i, k in enumerate(keys))

    # 策略对比: 各策略有效集群的时间分布 (±8 交易日容忍视为"同一事件")
    #
    # 日期轴必须用所有策略的并集, 不能用某一个策略的。各策略起点不同
    # (中证全指 2011-08 才发布, 上证综指能回到 2000), 若拿其中一个当轴,
    # 另一个早于该起点的集群会在 idx 里查不到 —— 匹配时被静默跳过, 画图时
    # 又被 .get(..., 0) 兜底成索引 0, 全堆在图最左端。那是错误的图, 不是
    # 少画几个点而已。
    cmp_section = ''
    if len(keys) >= 2:
        all_dates = sorted({d for k in keys for d in payloads[k]['series']['dates']})
        idx = {d: i for i, d in enumerate(all_dates)}
        TOL = 8

        def valid_of(k):
            return [c for c in payloads[k]['clusters'] if c['type'] == '有效集群']

        # 落在并集轴之外的集群不该发生(并集是所有策略日期的合集), 真出现了
        # 说明 export 的 dates 与 clusters 对不上, 宁可显式报出来也不要静默兜底
        missing = [(k, c['start']) for k in keys for c in valid_of(k)
                   if c['start'] not in idx or c['end'] not in idx]
        if missing:
            print(f'  ⚠ 策略对比: {len(missing)} 个集群的日期不在并集轴上, 已跳过: {missing[:3]}')

        # 裁掉最早一个集群之前的空白: 各策略起点差好几年, 不裁的话图左边
        # 会拖出一长条既无数据也无信号的留白, 把真正有内容的区间挤扁。
        first_i = min((idx[c['start']] for k in keys for c in valid_of(k)
                       if c['start'] in idx), default=0)
        clip = max(0, first_i - 60)          # 留 60 个交易日余量, 不要贴边
        all_dates = all_dates[clip:]
        idx = {d: i - clip for d, i in idx.items() if i >= clip}

        lane_colors = ['#D9A441', '#5C8AE6', '#4FA98C', '#C64B37', '#E0904A']
        lanes, per_key = [], {}
        for li, k in enumerate(keys):
            pk = payloads[k]
            items = []
            for c in valid_of(k):
                if c['start'] not in idx or c['end'] not in idx:
                    continue
                items.append(dict(id=c['id'], i0=idx[c['start']], i1=idx[c['end']],
                                  color=LEVEL_COLOR.get(c['level'], '#5C8AE6'),
                                  label=f'{pk["strategy"]} #{c["id"]} · {c["level"]}',
                                  sublabel=f'{c["start"]} ~ {c["end"]} · {c["count"]}次信号'))
            per_key[k] = items
            lanes.append(dict(label=pk['strategy'],
                              color=lane_colors[li % len(lane_colors)], items=items))

        # 相邻泳道之间连线, 表示时间上重叠(容忍 ±TOL 个交易日)的集群
        links = []
        for li in range(len(keys) - 1):
            for ca in per_key[keys[li]]:
                for cb in per_key[keys[li + 1]]:
                    if not (ca['i1'] + TOL < cb['i0'] or cb['i1'] + TOL < ca['i0']):
                        links.append([li, ca['id'], li + 1, cb['id']])

        def _base_of(pk, field):
            ba = pk['meta'].get('baseline_all') or {}
            gw = pk['params']['grade_window']
            # JSON 对象键一律是字符串, Python 侧 baseline_all 用的是 int 键,
            # 往返后会变成 '252'。两种都试。
            return (ba.get(str(gw)) or ba.get(gw) or {}).get(field)

        head_cells = ''.join(f'<th>{payloads[k]["strategy"]}</th>' for k in keys)
        cmp_metrics = [
            ('回测区间', lambda pk: f'{pk["meta"]["start"]} ~ {pk["meta"]["end"]}'),
            ('趋势基准', lambda pk: pk['index_source']),
            ('有效集群数', lambda pk: pk['meta']['n_clusters_valid']),
            ('中型命中率', lambda pk: _pct(pk['meta']['hit_rate'])),
            ('中型基准率', lambda pk: _pct(_base_of(pk, 'p_mid'))),
            ('较大命中率', lambda pk: _pct(pk['meta'].get('hit_rate_big'))),
            ('较大基准率', lambda pk: _pct(_base_of(pk, 'p_big'))),
        ]
        cmp_rows = '\n'.join(
            f'<tr><td class="strong">{label}</td>'
            + ''.join(f'<td class="num">{fn(payloads[k])}</td>' for k in keys)
            + '</tr>' for label, fn in cmp_metrics)

        cmp_section = f"""
<section class="section" id="compare">
  <div class="section-head"><span class="section-num">六</span><h2>策略对比</h2></div>
  <p class="section-sub">泳道图对比各策略有效集群的时间分布，虚线连接相邻泳道中时间重叠的集群（容忍 ±{TOL} 个交易日）。
  各策略回测区间不同（见下表），且<b>回撤是各自基准指数的回撤</b>，命中率不能跨策略直接比大小。</p>
  <div id="chart-timeline"></div>
  <div style="height:16px"></div>
  <div class="tbl-wrap"><div class="tbl-scroll"><table class="rpt cmp-tbl">
    <thead><tr><th>指标</th>{head_cells}</tr></thead>
    <tbody>{cmp_rows}</tbody></table></div></div>
</section>
<script>window.__TIMELINE__ = {json.dumps(dict(dates=all_dates, lanes=lanes, links=links), ensure_ascii=False)};</script>
"""

    html = _head('兴登堡预兆 · 回测仪表盘', DASHBOARD_CSS) + f"""
<nav class="topnav">
  <span class="brand">HINDENBURG·DASHBOARD</span>
  <a href="#summary">核心结论</a><a href="#trend">指数走势</a>
  <a href="#panel">四条件面板</a><a href="#history">信号回溯</a>
  <a href="#stats">有效性统计</a>{'<a href="#compare">策略对比</a>' if cmp_section else ''}
</nav>

<header class="hero">
  <h1>兴登堡预兆<br>回测<em>仪表盘</em></h1>
  <div class="meta-row">
    <span><b>样本区间</b>：<span id="hero-range"></span></span>
  </div>
</header>

<div class="section" style="padding-bottom:0">
  <div class="toggle-row">
    <div class="toggle-group">{btns}</div>
    <span class="toggle-caption" id="toggle-caption"></span>
  </div>
</div>

<section class="section" id="status">
  <div class="section-head"><span class="section-num">·</span><h2>当前状态</h2></div>
  <p class="section-sub">现状快照，不是回测统计</p>
  <div class="stat-grid" id="status-grid"></div>
</section>

<section class="section" id="summary">
  <div class="section-head"><span class="section-num">一</span><h2>核心结论</h2></div>
  <div class="stat-grid" id="stat-grid"></div>
  <p class="section-sub" style="margin-bottom:10px">集群信号后续回撤等级分布</p>
  <div class="pill-row" id="pill-row"></div>
  <div class="section-note warn" id="summary-note"></div>
</section>

<section class="section" id="trend">
  <div class="section-head"><span class="section-num">二</span><h2 id="trend-title">指数走势与信号标注</h2></div>
  <p class="section-sub">按后续回撤分级着色；三角=有效集群，圆点=单点信号</p>
  <div id="chart-trend"></div>
</section>

<section class="section" id="panel">
  <div class="section-head"><span class="section-num">三</span><h2>四条件监控面板</h2></div>
  <p class="section-sub" id="panel-sub"></p>
  <div class="chart-grid cols-2-2">
    <div id="chart-cond1"></div><div id="chart-cond2"></div>
    <div id="chart-cond3"></div><div id="chart-cond4"></div>
  </div>
</section>

<section class="section" id="history">
  <div class="section-head"><span class="section-num">四</span><h2>信号历史回溯表</h2></div>
  <p class="section-sub" id="history-sub"></p>
  <div class="tbl-wrap"><div class="tbl-scroll"><table class="rpt">
    <thead><tr><th>集群</th><th>预警区间</th><th>特征数</th><th>类型</th><th id="history-ddhead">回撤</th><th>等级</th></tr></thead>
    <tbody id="history-tbody"></tbody></table></div></div>
</section>

<section class="section" id="stats">
  <div class="section-head"><span class="section-num">五</span><h2>集群有效性统计</h2></div>
  <p class="section-sub" id="stats-sub"></p>
  <div id="chart-box"></div>
</section>

{cmp_section}

<footer>
  <p><b>局限</b>：可评估样本量小，命中率置信区间宽；回撤口径为<span id="foot-ddmode"></span>；不构成投资建议。</p>
</footer>

<script>{_asset('chart_engine.js')}</script>
<script>{_asset('backtest.js')}</script>
<script>
window.__DATA__ = {json.dumps(payloads, ensure_ascii=False)};
window.__LEVEL_COLOR__ = {json.dumps(LEVEL_COLOR, ensure_ascii=False)};
</script>
<script>
{DASHBOARD_JS}
</script>
</body></html>"""

    out_path.write_text(html, encoding='utf-8')
    return out_path


DASHBOARD_JS = r"""
(function(){
  const D = window.__DATA__, LC = window.__LEVEL_COLOR__;
  const TAG = {'较大回撤':'tag-red','中型回调':'tag-orange','小幅回调':'tag-amber',
               '无显著回撤':'tag-teal','近期信号':'tag-blue'};
  let charts = [];
  const pct = (x,d) => (x===null||x===undefined) ? '—' : (x*100).toFixed(d===undefined?1:d)+'%';


  function render(key){
    charts.forEach(c => c.destroy && c.destroy());
    charts = [];
    const d = D[key], S = d.series, m = d.meta, p = d.params;

    document.getElementById('toggle-caption').textContent =
      `${d.strategy} · ${d.index_source} · 集群窗口${p.cluster_window}日`;
    document.querySelectorAll('.toggle-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.cfg === key));

    // 各策略的区间不同(指数发布日/成分股起点都不一样), 头部无数据的那段
    // 已经在 Python 侧裁掉了, 这里直接展示实际区间即可。
    document.getElementById('hero-range').textContent =
      `${m.start} ~ ${m.end}（${m.n_days.toLocaleString()} 个交易日）`;

    // 口径说明按实际参数生成, 不写死 —— 之前写死的那句和默认参数正好说反了
    document.getElementById('foot-ddmode').textContent =
      (p.dd_mode === 'peak_trough' ? '窗口内峰谷最大回撤' : '锚点日收盘起最低点跌幅') +
      `（起算点：${p.dd_anchor === 'confirm' ? '集群确认日' : '集群首日'}）`;

    // 当前状态 —— 现状快照, 不是回测统计
    const st = m.status || {};
    const ac = st.active_cluster;
    document.getElementById('status-grid').innerHTML = `
      <div class="stat-card"><div class="label">最近信号日</div>
        <div class="value" style="font-size:20px">${st.last_signal_date || '无'}</div>
        <div class="foot">截至 ${st.as_of || '-'}</div></div>
      <div class="stat-card"><div class="label">近 ${st.window} 日单点信号</div>
        <div class="value">${st.recent_signal_count ?? 0}</div>
        <div class="foot">不要求同属一个集群</div></div>
      <div class="stat-card"><div class="label">近期集群</div>
        <div class="value" style="font-size:16px">${ac ? `${ac.type}` : '无'}</div>
        <div class="foot">${ac
          ? `${ac.start} ~ ${ac.end}，${ac.count} 次信号` +
            (ac.confirm_date ? `，已于 ${ac.confirm_date} 确认` : '，尚未确认')
          : `近 ${st.window} 日内无集群`}</div></div>`;

    const gw = p.grade_window, cuts = p.grade_cuts;
    document.getElementById('stat-grid').innerHTML = `
      <div class="stat-card"><div class="label">集群总数</div>
        <div class="value">${m.n_clusters_valid}</div>
        <div class="foot">含 ${m.n_clusters_valid - m.n_gradable} 个近期信号</div></div>
      <div class="stat-card"><div class="label">中型以上回撤命中</div>
        <div class="value">${m.hit_mid}</div>
        <div class="foot">${gw} 日 ≤ ${(cuts[1]*100).toFixed(0)}%</div></div>
      <div class="stat-card"><div class="label">较大回撤命中</div>
        <div class="value">${m.hit_big}</div>
        <div class="foot">${gw} 日 ≤ ${(cuts[0]*100).toFixed(0)}%</div></div>
      <div class="stat-card"><div class="label">中型回撤命中率</div>
        <div class="value">${pct(m.hit_rate,0)}</div>
        <div class="foot">基于 ${m.n_gradable} 个可评估集群</div></div>`;

    const order = ['较大回撤','中型回调','小幅回调','无显著回撤','近期信号'];
    document.getElementById('pill-row').innerHTML = order.filter(l => m.level_counts[l])
      .map(l => `<div class="pill"><span class="dot" style="background:${LC[l]}"></span>${l} <b>${m.level_counts[l]}</b></div>`).join('');

    document.getElementById('summary-note').innerHTML =
      `集群信号为市场系统性调整的<b>必要非充分条件</b>（触发 ≠ 必跌，但下跌概率抬升），` +
      `触发时建议提升防御仓位而非直接卖出。存在"假警报"（如流动性宽松期），需结合宏观环境综合判断。` +
      `当前可评估集群 ${m.n_gradable} 个，样本量有限，结论不宜过度外推。`;

    // 走势 —— 逐日标注: 每个信号日一个点, 颜色按它自己的回撤等级;
    // 属于某个有效集群的额外用三角形强调, 孤立的单点信号用小圆点。
    document.getElementById('trend-title').textContent = d.index_source + ' 走势与信号标注';
    const clusterById = {};
    d.clusters.filter(c => c.type==='有效集群').forEach(c => { clusterById[c.id] = c; });
    const markers = (d.signal_days || []).map(sd => {
      const c = sd.cluster_id != null ? clusterById[sd.cluster_id] : null;
      return {index: sd.index, color: LC[sd.level]||'#5C8AE6',
        shape: c ? 'triangle' : 'dot', r: c ? 6 : 3,
        label: c ? `集群#${c.id} · ${sd.level}` : `单点信号 · ${sd.level}`,
        sublabel: c ? `${c.start} ~ ${c.end} · ${c.count}次信号` : sd.date};
    });
    charts.push(new CE.TimeSeriesChart(document.getElementById('chart-trend'), {
      title: d.index_source, subtitle:'可滚轮缩放/拖拽 · 三角=有效集群 圆点=单点信号', dates:S.dates, height:340,
      series:[{label:d.index_source, color:'#1B2233', data:S.index_close, width:1.5}],
      markers, yFormat:v=>v.toFixed(0)}));

    const ma = BT.rollingMean(S.index_close, p.ma_period);
    charts.push(new CE.TimeSeriesChart(document.getElementById('chart-cond1'), {
      title:`条件① 趋势向上`, subtitle:`${d.index_source} vs ${p.ma_period}日均线`, dates:S.dates, height:230,
      series:[{label:'指数',color:'#97A3BC',data:S.index_close,width:1.1},
              {label:`MA${p.ma_period}`,color:'#D9A441',data:ma,width:1.8}],
      yFormat:v=>v.toFixed(0)}));
    charts.push(new CE.TimeSeriesChart(document.getElementById('chart-cond2'), {
      title:'条件② 新高/新低占比', subtitle:`阈值 ${(p.threshold*100).toFixed(1)}%`, dates:S.dates, height:230,
      series:[{label:'新高占比',color:'#C64B37',data:S.high_ratio,width:1.3},
              {label:'新低占比',color:'#5C8AE6',data:S.low_ratio,width:1.3}],
      hlines:[{value:p.threshold,color:'#D9A441',label:'阈值'}], yFormat:v=>(v*100).toFixed(1)+'%'}));
    charts.push(new CE.TimeSeriesChart(document.getElementById('chart-cond3'), {
      title:`条件③ 新高 ≤ 新低×${p.hl_ratio}`, subtitle:'柱=新高家数', dates:S.dates, height:230,
      series:[{label:'新高家数',color:'#C64B37',data:S.new_high,type:'bar',opacity:0.75},
              {label:'阈值',color:'#5C8AE6',data:S.new_low.map(v=>v==null?null:v*p.hl_ratio),width:1.4,dash:'4,3'}],
      yFormat:v=>v.toFixed(0)}));
    charts.push(new CE.TimeSeriesChart(document.getElementById('chart-cond4'), {
      title:'条件④ McClellan振荡器', subtitle:`EMA${p.ema_short}-EMA${p.ema_long}，0轴之下=满足`, dates:S.dates, height:230,
      series:[{label:'McClellan',color:'#E0904A',data:S.mcclellan,type:'bar'}],
      hlines:[{value:0,color:'#9AA2B5',label:'0轴'}], yFormat:v=>v.toFixed(0)}));

    document.getElementById('panel-sub').textContent =
      `条件① ${pct(m.cond_rate.cond1,1)} · 条件② ${pct(m.cond_rate.cond2,1)} · ` +
      `条件③ ${pct(m.cond_rate.cond3,1)} · 条件④ ${pct(m.cond_rate.cond4,1)} · ` +
      `四条件同时满足 ${m.signal_days} 天 / ${m.n_days.toLocaleString()} 天`;

    document.getElementById('history-ddhead').textContent = `${gw}日回撤`;
    document.getElementById('history-sub').textContent =
      `"特征数"为集群内信号触发次数；"${gw}日回撤"为锚点日起未来 ${gw} 个交易日的` +
      (p.dd_mode === 'peak_trough' ? '峰谷最大回撤' : '最低点跌幅');
    document.getElementById('history-tbody').innerHTML = d.clusters.map(c => {
      const dd = c['dd'+gw];
      return `<tr><td class="num">${c.id}</td><td class="strong">${c.start} ~ ${c.end}</td>
        <td class="num">${c.count}</td>
        <td><span class="tag ${c.type==='有效集群'?'tag-amber':'tag-blue'}">${c.type}</span></td>
        <td class="num">${dd===null?'观察中':pct(dd,1)}</td>
        <td><span class="tag ${TAG[c.level]||'tag-blue'}">${c.level}</span></td></tr>`;
    }).join('');

    // 箱线图直接用 Python 预算好的分位数。这里以前调 BT.runAll() 重算一遍,
    // 而 runAll 内部的 baselineRates 要扫全样本 x 4 个窗口 —— 切换策略时
    // 每次都重跑, 那就是切换卡顿的来源。
    document.getElementById('stats-sub').textContent =
      `对有效集群锚点日起未来各窗口最大回撤的分布, 基于 ${m.n_gradable} 个观察期已满的集群`;
    const groups = p.dd_windows.filter(w=>m.cluster_box[w])
      .map(w => ({label:`未来${w}日`, ...m.cluster_box[w], color:'#D9A441'}));
    if (groups.length) charts.push(new CE.BoxPlotChart(document.getElementById('chart-box'), {
      title:'集群信号后续最大回撤分布', subtitle:`基于 ${m.n_gradable} 个集群`,
      groups, yFormat:v=>(v*100).toFixed(0)+'%'}));

  }

  document.querySelectorAll('.toggle-btn').forEach(b =>
    b.addEventListener('click', () => render(b.dataset.cfg)));
  render(Object.keys(D)[0]);

  if (window.__TIMELINE__){
    new CE.TimelineCompare(document.getElementById('chart-timeline'), {
      title:'各策略有效集群时间分布', subtitle:'虚线=判定为同一事件的匹配',
      dates:window.__TIMELINE__.dates, lanes:window.__TIMELINE__.lanes, links:window.__TIMELINE__.links});
  }
})();
"""


def build_all(payloads: dict, out_dir: Path) -> list[Path]:
    """只产出 dashboard.html。参数实验室(lab.html)已经独立成 lab.py, 要生成
    的话单独运行 `python lab.py`, 不在这个默认流程里。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return [build_dashboard(payloads, out_dir / 'dashboard.html')]
