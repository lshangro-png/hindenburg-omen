"""
生成回测仪表盘：固定参数下的完整结果，可切换策略，并有策略对比。

同一份代码生成中英两个版本：dashboard.html（中文）和 dashboard_en.html（英文）。
页面上的文字都在 assets/i18n.js 的对照表里，这里只负责页面结构和数据。
页面是单个自包含的 HTML，图表引擎和样式来自 assets/，数据以 JSON 内嵌。
参数实验室由 lab.py 单独生成。
"""
from __future__ import annotations

import json
from pathlib import Path

ASSETS = Path(__file__).parent / 'assets'
LANGS = ('zh', 'en')

# 回撤等级 → 颜色。键必须和 backtest.grade() 的返回值一致
LEVEL_COLOR = {'较大回撤': '#C64B37', '中型回调': '#E0904A', '小幅回调': '#D9A441',
               '无显著回撤': '#4FA98C', '近期信号': '#5C8AE6'}

# 网页标题（<title> 在 JS 运行前就要有，所以单独放在这里）
TITLES = {
    'dashboard': {'zh': '兴登堡预兆 · 回测仪表盘', 'en': 'Hindenburg Omen · Backtest Dashboard'},
    'lab': {'zh': '兴登堡预兆 · 参数实验室', 'en': 'Hindenburg Omen · Parameter Lab'},
}


def page_name(kind: str, lang: str) -> str:
    """页面文件名：dashboard.html / dashboard_en.html / lab.html / lab_en.html"""
    return f'{kind}.html' if lang == 'zh' else f'{kind}_{lang}.html'


def _pct(x, d=0):
    return '—' if x is None else f'{x*100:.{d}f}%'


def _asset(name: str) -> str:
    return (ASSETS / name).read_text(encoding='utf-8')


def toggle_buttons(payloads: dict) -> str:
    """策略切换按钮的 HTML（第一个默认选中；按钮文字由 i18n.js 按语言填充）。"""
    return '\n'.join(
        f'<button class="toggle-btn{" active" if i == 0 else ""}" data-cfg="{k}" type="button" '
        f'data-v="{payloads[k]["strategy"]}">{payloads[k]["strategy"]}</button>'
        for i, k in enumerate(payloads))


def lang_link(kind: str, lang: str) -> str:
    """导航栏右侧切换到另一种语言的链接。"""
    other = 'en' if lang == 'zh' else 'zh'
    return (f'<a class="lang-switch" href="{page_name(kind, other)}" '
            f'data-i18n="lang_switch"></a>')


def _head(title: str, extra_css: str = '', lang: str = 'zh') -> str:
    """HTML 头部：字体、内联 report.css 和额外样式。"""
    html_lang = 'zh-CN' if lang == 'zh' else 'en'
    return f"""<!DOCTYPE html>
<html lang="{html_lang}">
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
.topnav .lang-switch{{margin-left:auto;}}
</style>
</head>
<body>"""


def _scripts(lang: str, data_js: str, page_js: str) -> str:
    """页面末尾的脚本：先定语言和对照表，再加载图表引擎、回测镜像、共用工具和页面逻辑。"""
    return f"""
<script>window.__LANG__ = {json.dumps(lang)};</script>
<script>{_asset('i18n.js')}</script>
<script>{_asset('chart_engine.js')}</script>
<script>{_asset('backtest.js')}</script>
<script>{_asset('common.js')}</script>
<script>
{data_js}
</script>
<script>
I18N.apply();
{page_js}
</script>
</body></html>"""


# 仪表盘专用样式（策略切换按钮、对比表）
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

def build_dashboard(payloads: dict, out_path: Path, lang: str = 'zh') -> Path:
    """payloads: {策略键: export_payload() 的结果}，按 lang 写出仪表盘。"""
    keys = list(payloads)

    btns = toggle_buttons(payloads)

    # 策略对比：泳道图展示各策略有效集群的时间分布，±TOL 个交易日内重叠视为同一事件。
    # 日期轴必须用所有策略日期的并集（各策略起点不同），否则早于某策略起点的集群
    # 会找不到位置。
    cmp_section = ''
    if len(keys) >= 2:
        all_dates = sorted({d for k in keys for d in payloads[k]['series']['dates']})
        idx = {d: i for i, d in enumerate(all_dates)}
        TOL = 8

        def valid_of(k):
            return [c for c in payloads[k]['clusters'] if c['type'] == '有效集群']

        missing = [(k, c['start']) for k in keys for c in valid_of(k)
                   if c['start'] not in idx or c['end'] not in idx]
        if missing:
            print(f'  ⚠ 策略对比: {len(missing)} 个集群的日期不在并集轴上, 已跳过: {missing[:3]}')

        # 裁掉最早集群之前的空白，左侧留 60 个交易日
        first_i = min((idx[c['start']] for k in keys for c in valid_of(k)
                       if c['start'] in idx), default=0)
        clip = max(0, first_i - 60)
        all_dates = all_dates[clip:]
        idx = {d: i - clip for d, i in idx.items() if i >= clip}

        # 泳道数据只放原始字段，显示文字由页面 JS 按语言拼出
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
                                  strategy=pk['strategy'], level=c['level'],
                                  start=c['start'], end=c['end'], count=c['count']))
            per_key[k] = items
            lanes.append(dict(label=pk['strategy'],
                              color=lane_colors[li % len(lane_colors)], items=items))

        # 相邻泳道中时间重叠的集群连线
        links = []
        for li in range(len(keys) - 1):
            for ca in per_key[keys[li]]:
                for cb in per_key[keys[li + 1]]:
                    if not (ca['i1'] + TOL < cb['i0'] or cb['i1'] + TOL < ca['i0']):
                        links.append([li, ca['id'], li + 1, cb['id']])

        def _base_of(pk, field):
            ba = pk['meta'].get('baseline_all') or {}
            gw = pk['params']['grade_window']
            # JSON 往返后 int 键会变成字符串，两种都试
            return (ba.get(str(gw)) or ba.get(gw) or {}).get(field)

        head_cells = ''.join(f'<th data-v="{payloads[k]["strategy"]}"></th>' for k in keys)
        cmp_metrics = [
            ('cmp_period', lambda pk: f'{pk["meta"]["start"]} ~ {pk["meta"]["end"]}'),
            ('cmp_bench', lambda pk: f'<span data-v="{pk["index_source"]}"></span>'),
            ('cmp_nvalid', lambda pk: pk['meta']['n_clusters_valid']),
            ('cmp_hit_mid', lambda pk: _pct(pk['meta']['hit_rate'])),
            ('cmp_base_mid', lambda pk: _pct(_base_of(pk, 'p_mid'))),
            ('cmp_hit_big', lambda pk: _pct(pk['meta'].get('hit_rate_big'))),
            ('cmp_base_big', lambda pk: _pct(_base_of(pk, 'p_big'))),
        ]
        cmp_rows = '\n'.join(
            f'<tr><td class="strong" data-i18n="{key}"></td>'
            + ''.join(f'<td class="num">{fn(payloads[k])}</td>' for k in keys)
            + '</tr>' for key, fn in cmp_metrics)

        cmp_section = f"""
<section class="section" id="compare">
  <div class="section-head"><span class="section-num" data-i18n="num6"></span><h2 data-i18n="sec_compare"></h2></div>
  <p class="section-sub" data-i18n="compare_sub" data-arg="{TOL}"></p>
  <div id="chart-timeline"></div>
  <div style="height:16px"></div>
  <div class="tbl-wrap"><div class="tbl-scroll"><table class="rpt cmp-tbl">
    <thead><tr><th data-i18n="cmp_metric"></th>{head_cells}</tr></thead>
    <tbody>{cmp_rows}</tbody></table></div></div>
</section>
<script>window.__TIMELINE__ = {json.dumps(dict(dates=all_dates, lanes=lanes, links=links), ensure_ascii=False)};</script>
"""

    html = _head(TITLES['dashboard'][lang], DASHBOARD_CSS, lang) + f"""
<nav class="topnav">
  <span class="brand">HINDENBURG·DASHBOARD</span>
  <a href="#summary" data-i18n="nav_summary"></a><a href="#trend" data-i18n="nav_trend"></a>
  <a href="#panel" data-i18n="nav_panel"></a><a href="#history" data-i18n="nav_history"></a>
  <a href="#stats" data-i18n="nav_stats"></a>{'<a href="#compare" data-i18n="nav_compare"></a>' if cmp_section else ''}
  <a href="{page_name('lab', lang)}">LAB</a>
  {lang_link('dashboard', lang)}
</nav>

<header class="hero">
  <h1 data-i18n="hero_dash"></h1>
  <div class="meta-row">
    <span><b data-i18n="sample_period"></b><span data-i18n="colon"></span><span id="hero-range"></span></span>
  </div>
</header>

<div class="section" style="padding-bottom:0">
  <div class="toggle-row">
    <div class="toggle-group">{btns}</div>
    <span class="toggle-caption" id="toggle-caption"></span>
  </div>
</div>

<section class="section" id="status">
  <div class="section-head"><span class="section-num">·</span><h2 data-i18n="sec_status"></h2></div>
  <p class="section-sub" data-i18n="sec_status_sub"></p>
  <div class="stat-grid" id="status-grid"></div>
</section>

<section class="section" id="summary">
  <div class="section-head"><span class="section-num" data-i18n="num1"></span><h2 data-i18n="sec_summary"></h2></div>
  <div class="stat-grid" id="stat-grid"></div>
  <p class="section-sub" style="margin-bottom:10px" data-i18n="sec_summary_sub"></p>
  <div class="pill-row" id="pill-row"></div>
  <div class="section-note warn" id="summary-note"></div>
</section>

<section class="section" id="trend">
  <div class="section-head"><span class="section-num" data-i18n="num2"></span><h2 id="trend-title"></h2></div>
  <p class="section-sub" data-i18n="sec_trend_sub"></p>
  <div id="chart-trend"></div>
</section>

<section class="section" id="panel">
  <div class="section-head"><span class="section-num" data-i18n="num3"></span><h2 data-i18n="sec_panel"></h2></div>
  <p class="section-sub" id="panel-sub"></p>
  <div class="chart-grid cols-2-2">
    <div id="chart-cond1"></div><div id="chart-cond2"></div>
    <div id="chart-cond3"></div><div id="chart-cond4"></div>
  </div>
</section>

<section class="section" id="history">
  <div class="section-head"><span class="section-num" data-i18n="num4"></span><h2 data-i18n="sec_history"></h2></div>
  <p class="section-sub" id="history-sub"></p>
  <div class="tbl-wrap"><div class="tbl-scroll"><table class="rpt">
    <thead><tr><th data-i18n="col_cluster"></th><th data-i18n="col_period"></th><th data-i18n="col_count"></th><th data-i18n="col_type"></th><th id="history-ddhead"></th><th data-i18n="col_grade"></th></tr></thead>
    <tbody id="history-tbody"></tbody></table></div></div>
</section>

<section class="section" id="stats">
  <div class="section-head"><span class="section-num" data-i18n="num5"></span><h2 data-i18n="sec_stats"></h2></div>
  <p class="section-sub" id="stats-sub"></p>
  <div id="chart-box"></div>
</section>

{cmp_section}

<footer>
  <p id="foot"></p>
</footer>
""" + _scripts(lang, f"""window.__DATA__ = {json.dumps(payloads, ensure_ascii=False)};
window.__LEVEL_COLOR__ = {json.dumps(LEVEL_COLOR, ensure_ascii=False)};""", DASHBOARD_JS)

    out_path.write_text(html, encoding='utf-8')
    return out_path


DASHBOARD_JS = r"""
(function(){
  const D = window.__DATA__, LC = window.__LEVEL_COLOR__;
  const {TAG, pct} = window.HO;
  const {t, v} = window.I18N;
  let charts = [];


  function render(key){
    charts.forEach(c => c.destroy && c.destroy());
    charts = [];
    const d = D[key], S = d.series, m = d.meta, p = d.params;
    const src = v(d.index_source);

    document.getElementById('toggle-caption').textContent = t('caption', v(d.strategy), src, p.cluster_window);
    document.querySelectorAll('.toggle-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.cfg === key));

    document.getElementById('hero-range').textContent = t('range', m.start, m.end, m.n_days.toLocaleString());

    // 口径说明按实际参数生成
    document.getElementById('foot').innerHTML = t('foot',
      (p.dd_mode === 'peak_trough' ? t('dd_peak') : t('dd_from')) + t('anchor', p.dd_anchor === 'confirm'));

    // 当前状态（现状快照，不是回测统计）
    const st = m.status || {};
    const ac = st.active_cluster;
    document.getElementById('status-grid').innerHTML = `
      <div class="stat-card"><div class="label">${t('st_last')}</div>
        <div class="value" style="font-size:20px">${st.last_signal_date || t('none')}</div>
        <div class="foot">${t('st_asof', st.as_of || '-')}</div></div>
      <div class="stat-card"><div class="label">${t('st_recent', st.window)}</div>
        <div class="value">${st.recent_signal_count ?? 0}</div>
        <div class="foot">${t('st_recent_foot')}</div></div>
      <div class="stat-card"><div class="label">${t('st_cluster')}</div>
        <div class="value" style="font-size:16px">${ac ? v(ac.type) : t('none')}</div>
        <div class="foot">${ac
          ? t('st_cluster_foot', ac.start, ac.end, ac.count) +
            (ac.confirm_date ? t('st_confirmed', ac.confirm_date) : t('st_unconfirmed'))
          : t('st_no_cluster', st.window)}</div></div>`;

    const gw = p.grade_window, cuts = p.grade_cuts;
    document.getElementById('stat-grid').innerHTML = `
      <div class="stat-card"><div class="label">${t('sc_total')}</div>
        <div class="value">${m.n_clusters_valid}</div>
        <div class="foot">${t('sc_total_foot', m.n_clusters_valid - m.n_gradable)}</div></div>
      <div class="stat-card"><div class="label">${t('sc_mid')}</div>
        <div class="value">${m.hit_mid}</div>
        <div class="foot">${t('sc_win', gw, (cuts[1]*100).toFixed(0))}</div></div>
      <div class="stat-card"><div class="label">${t('sc_big')}</div>
        <div class="value">${m.hit_big}</div>
        <div class="foot">${t('sc_win', gw, (cuts[0]*100).toFixed(0))}</div></div>
      <div class="stat-card"><div class="label">${t('sc_rate')}</div>
        <div class="value">${pct(m.hit_rate,0)}</div>
        <div class="foot">${t('sc_rate_foot', m.n_gradable)}</div></div>`;

    const order = ['较大回撤','中型回调','小幅回调','无显著回撤','近期信号'];
    document.getElementById('pill-row').innerHTML = order.filter(l => m.level_counts[l])
      .map(l => `<div class="pill"><span class="dot" style="background:${LC[l]}"></span>${v(l)} <b>${m.level_counts[l]}</b></div>`).join('');

    const base = (m.baseline_all || {})[gw];
    document.getElementById('summary-note').innerHTML =
      t('summary_note', m.n_gradable, pct(m.hit_rate,0), pct(base ? base.p_mid : null, 0));

    // 走势与逐日信号标注
    document.getElementById('trend-title').textContent = t('trend_title', src);
    const markers = HO.signalMarkers(d.signal_days, d.clusters, LC);
    charts.push(new CE.TimeSeriesChart(document.getElementById('chart-trend'), {
      title: src, subtitle: t('trend_sub'), dates:S.dates, height:340,
      series:[{label:src, color:'#1B2233', data:S.index_close, width:1.5}],
      markers, yFormat:v=>v.toFixed(0)}));

    const ma = BT.rollingMean(S.index_close, p.ma_period);
    charts.push(new CE.TimeSeriesChart(document.getElementById('chart-cond1'), {
      title:t('c1_title'), subtitle:t('c1_sub', src, p.ma_period), dates:S.dates, height:230,
      series:[{label:t('index'),color:'#97A3BC',data:S.index_close,width:1.1},
              {label:`MA${p.ma_period}`,color:'#D9A441',data:ma,width:1.8}],
      yFormat:v=>v.toFixed(0)}));
    charts.push(new CE.TimeSeriesChart(document.getElementById('chart-cond2'), {
      title:t('c2_title'), subtitle:t('c2_sub', (p.threshold*100).toFixed(1)), dates:S.dates, height:230,
      series:[{label:t('new_high_pct'),color:'#C64B37',data:S.high_ratio,width:1.3},
              {label:t('new_low_pct'),color:'#5C8AE6',data:S.low_ratio,width:1.3}],
      hlines:[{value:p.threshold,color:'#D9A441',label:t('threshold')}], yFormat:v=>(v*100).toFixed(1)+'%'}));
    charts.push(new CE.TimeSeriesChart(document.getElementById('chart-cond3'), {
      title:t('c3_title', p.hl_ratio), subtitle:t('c3_sub'), dates:S.dates, height:230,
      series:[{label:t('new_high_n'),color:'#C64B37',data:S.new_high,type:'bar',opacity:0.75},
              {label:t('threshold'),color:'#5C8AE6',data:S.new_low.map(v=>v==null?null:v*p.hl_ratio),width:1.4,dash:'4,3'}],
      yFormat:v=>v.toFixed(0)}));
    charts.push(new CE.TimeSeriesChart(document.getElementById('chart-cond4'), {
      title:t('c4_title'), subtitle:t('c4_sub', p.ema_short, p.ema_long), dates:S.dates, height:230,
      series:[{label:'McClellan',color:'#E0904A',data:S.mcclellan,type:'bar'}],
      hlines:[{value:0,color:'#9AA2B5',label:t('zero_axis')}], yFormat:v=>v.toFixed(0)}));

    document.getElementById('panel-sub').textContent = t('panel_sub',
      pct(m.cond_rate.cond1,1), pct(m.cond_rate.cond2,1), pct(m.cond_rate.cond3,1), pct(m.cond_rate.cond4,1),
      m.signal_days, m.n_days.toLocaleString());

    document.getElementById('history-ddhead').textContent = t('dd_head', gw);
    document.getElementById('history-sub').textContent = t('history_sub', gw, p.dd_mode === 'peak_trough');
    document.getElementById('history-tbody').innerHTML = d.clusters.map(c => {
      const dd = c['dd'+gw];
      return `<tr><td class="num">${c.id}</td><td class="strong">${c.start} ~ ${c.end}</td>
        <td class="num">${c.count}</td>
        <td><span class="tag ${c.type==='有效集群'?'tag-amber':'tag-blue'}">${v(c.type)}</span></td>
        <td class="num">${dd===null?t('pending_dd'):pct(dd,1)}</td>
        <td><span class="tag ${TAG[c.level]||'tag-blue'}">${v(c.level)}</span></td></tr>`;
    }).join('');

    // 箱线图直接用 Python 算好的分位数，切换策略时不必重算基准率
    document.getElementById('stats-sub').textContent = t('stats_sub', m.n_gradable);
    const groups = p.dd_windows.filter(w=>m.cluster_box[w])
      .map(w => ({label:t('fwd', w), ...m.cluster_box[w], color:'#D9A441'}));
    if (groups.length) charts.push(new CE.BoxPlotChart(document.getElementById('chart-box'), {
      title:t('box_title'), subtitle:t('box_sub', m.n_gradable),
      groups, yFormat:v=>(v*100).toFixed(0)+'%'}));

  }

  document.querySelectorAll('.toggle-btn').forEach(b =>
    b.addEventListener('click', () => render(b.dataset.cfg)));
  render(Object.keys(D)[0]);

  if (window.__TIMELINE__){
    const TL = window.__TIMELINE__;
    const lanes = TL.lanes.map(lane => ({...lane, label: v(lane.label),
      items: lane.items.map(it => ({...it,
        label: `${v(it.strategy)} #${it.id} · ${v(it.level)}`,
        sublabel: t('mk_range', it.start, it.end, it.count)}))}));
    new CE.TimelineCompare(document.getElementById('chart-timeline'), {
      title:t('tl_title'), subtitle:t('tl_sub'),
      dates:TL.dates, lanes, links:TL.links});
  }
})();
"""


def build_all(payloads: dict, out_dir: Path) -> list[Path]:
    """生成中英两版仪表盘（参数实验室由 lab.py 单独生成）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return [build_dashboard(payloads, out_dir / page_name('dashboard', lang), lang)
            for lang in LANGS]
