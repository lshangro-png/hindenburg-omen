"""
参数实验室：在浏览器里拖滑块实时重算轻参数。
生成中英两版：lab.html（中文）和 lab_en.html（英文），页面文字在 assets/i18n.js。

用法：
    python hindenburg.py export     # 先导出带中间序列的 JSON
    python lab.py                   # 读 output/export_*.json，生成 output/lab.html 和 lab_en.html
    python lab.py <目录>            # 指定别的导出目录

原理：导出的 JSON 带着中间序列，assets/backtest.js 用和 Python 相同的逻辑重算
四条件、集群和回撤。重参数决定中间序列本身，浏览器里算不了，只做只读展示。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import config
from viz import _head, _scripts, toggle_buttons, lang_link, page_name, LEVEL_COLOR, LANGS, TITLES

# 参数实验室专用样式
LAB_CSS = """
.lab-layout{display:grid; grid-template-columns:300px 1fr; gap:20px; align-items:start;}
@media (max-width:900px){ .lab-layout{grid-template-columns:1fr;} }
.lab-panel{background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding:18px 18px 8px; position:sticky; top:60px;}
.lab-panel h3{font-family:var(--font-display); font-size:15px; margin:0 0 4px;}
.lab-panel .hint{font-size:11.5px; color:var(--text-faint); margin:0 0 16px; line-height:1.5;}
.slider-row{margin-bottom:16px;}
.slider-row label{display:flex; justify-content:space-between; align-items:baseline;
  font-size:12.5px; color:var(--text-dim); margin-bottom:6px;}
.slider-row label b{font-family:var(--font-mono); color:var(--amber); font-size:13px;}
.slider-row input[type=range]{width:100%; accent-color:var(--amber); cursor:pointer;}
.slider-row .base{font-size:10.5px; color:var(--text-faint); font-family:var(--font-mono);}
.lab-actions{display:flex; gap:8px; margin:6px 0 14px;}
.lab-actions button{flex:1; font-family:var(--font-body); font-size:12.5px; padding:8px;
  border-radius:7px; border:1px solid var(--line); background:var(--panel);
  color:var(--text-dim); cursor:pointer; transition:.15s;}
.lab-actions button:hover{color:var(--amber); border-color:var(--amber);}
.heavy-box{background:rgba(92,138,230,.07); border:1px solid rgba(92,138,230,.3);
  border-radius:9px; padding:12px 14px; margin-top:6px;}
.heavy-box h4{font-size:12.5px; margin:0 0 8px; color:var(--blue);}
.heavy-box .kv{display:flex; justify-content:space-between; font-size:11.5px;
  font-family:var(--font-mono); color:var(--text-faint); padding:2px 0;}
.heavy-box .note{font-size:11px; color:var(--text-faint); margin-top:8px; line-height:1.5;}
.delta{font-family:var(--font-mono); font-size:12px; margin-left:6px;}
.delta.up{color:var(--teal);} .delta.down{color:var(--red);} .delta.flat{color:var(--text-faint);}
.lab-stat{display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:18px;}
.lab-stat .cell{background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px;}
.lab-stat .cell .lab{font-size:11.5px; color:var(--text-faint); margin-bottom:6px;}
.lab-stat .cell .val{font-family:var(--font-mono); font-size:22px; font-weight:600; color:var(--amber); line-height:1;}
"""


def build_lab(payloads: dict, out_path: Path, lang: str = 'zh') -> Path:
    """payloads: {键: 导出的 JSON}，按 lang 写出参数实验室页面。"""
    btns = toggle_buttons(payloads)
    sel_style = ('width:100%;padding:6px;border-radius:6px;background:var(--panel);'
                 'color:var(--text);border:1px solid var(--line);font-size:12.5px')

    html = _head(TITLES['lab'][lang], LAB_CSS, lang) + f"""
<nav class="topnav">
  <span class="brand">HINDENBURG·LAB</span>
  <a href="{page_name('dashboard', lang)}" data-i18n="lab_back"></a>
  {lang_link('lab', lang)}
</nav>

<header class="hero" style="padding-bottom:24px">
  <div class="eyebrow"><span class="beacon"></span><span data-i18n="lab_eyebrow"></span></div>
  <h1 data-i18n="hero_lab"></h1>
  <p class="sub" data-i18n="lab_intro"></p>
</header>

<div class="section" style="padding-bottom:0">
  <div class="toggle-row">
    <div class="toggle-group">{btns}</div>
    <span class="toggle-caption" id="lab-caption"></span>
  </div>
</div>

<div class="section">
<div class="lab-layout">
  <div class="lab-panel">
    <h3 data-i18n="light_params"></h3>
    <p class="hint" data-i18n="light_hint"></p>
    <div id="sliders"></div>
    <div class="slider-row">
      <label><span data-i18n="cluster_mode"></span><b id="val-cm"></b></label>
      <select id="sel-cm" style="{sel_style}">
        <option value="fixed" data-i18n="opt_fixed"></option>
        <option value="chain" data-i18n="opt_chain"></option>
      </select>
    </div>
    <div class="slider-row">
      <label><span data-i18n="dd_mode"></span><b id="val-dm"></b></label>
      <select id="sel-dm" style="{sel_style}">
        <option value="peak_trough" data-i18n="opt_peak"></option>
        <option value="from_signal" data-i18n="opt_from"></option>
      </select>
    </div>
    <div class="lab-actions">
      <button id="btn-reset" type="button" data-i18n="reset"></button>
    </div>
    <div class="heavy-box">
      <h4 data-i18n="heavy_title"></h4>
      <div id="heavy-list"></div>
      <p class="note" data-i18n="heavy_note"></p>
    </div>
  </div>

  <div>
    <div class="lab-stat" id="lab-stat"></div>
    <div class="section-note warn" id="lab-note" style="margin-bottom:18px"></div>
    <div id="lab-trend"></div>
    <div style="height:16px"></div>
    <div class="chart-grid cols-2-2">
      <div id="lab-cond1"></div><div id="lab-cond2"></div>
      <div id="lab-cond3"></div><div id="lab-cond4"></div>
    </div>
    <div style="height:16px"></div>
    <div id="lab-box"></div>
    <div style="height:16px"></div>
    <div class="tbl-wrap"><div class="tbl-scroll"><table class="rpt">
      <thead><tr><th data-i18n="col_cluster"></th><th data-i18n="col_period"></th><th data-i18n="col_count"></th><th data-i18n="col_type"></th><th data-i18n="col_drop"></th><th data-i18n="col_grade"></th></tr></thead>
      <tbody id="lab-tbody"></tbody></table></div></div>
  </div>
</div>
</div>

<footer>
  <p data-i18n="lab_foot1"></p>
  <p data-i18n="lab_foot2"></p>
</footer>
""" + _scripts(lang, f"""window.__DATA__ = {json.dumps(payloads, ensure_ascii=False)};
window.__SLIDERS__ = {json.dumps(config.SLIDER_SPECS, ensure_ascii=False)};
window.__LEVEL_COLOR__ = {json.dumps(LEVEL_COLOR, ensure_ascii=False)};""", LAB_JS)

    out_path.write_text(html, encoding='utf-8')
    return out_path


LAB_JS = r"""
(function(){
  const D = window.__DATA__, SPECS = window.__SLIDERS__, LC = window.__LEVEL_COLOR__;
  const {TAG, pct} = window.HO;
  const {t, v} = window.I18N;
  let key = Object.keys(D)[0];
  let params = null, baseline = null, charts = [];

  function defaults(){ return JSON.parse(JSON.stringify(D[key].params)); }

  // 滑块数值显示：百分比或「数值 + 单位」
  const fmt = (s, x) => s.pct ? (x*100).toFixed(1)+'%' : x + t('u_'+s.key);

  function buildSliders(){
    const box = document.getElementById('sliders');
    box.innerHTML = SPECS.map(s => {
      const base = D[key].params[s.key];
      const val = params[s.key];
      return `<div class="slider-row">
        <label>${t('sl_'+s.key)}<b id="val-${s.key}">${fmt(s, val)}</b></label>
        <input type="range" id="sl-${s.key}" min="${s.min}" max="${s.max}" step="${s.step}" value="${val}">
        <div class="base">${t('sl_default', fmt(s, base))}</div></div>`;
    }).join('');
    ['cm','dm'].forEach(k => {
      const key = k === 'cm' ? 'cluster_mode' : 'dd_mode';
      const sel = document.getElementById('sel-'+k);
      sel.value = params[key] || (k==='cm'?'fixed':'peak_trough');
      document.getElementById('val-'+k).textContent = sel.value;
      sel.onchange = e => {
        params[key] = e.target.value;
        document.getElementById('val-'+k).textContent = e.target.value;
        recompute();
      };
    });
    SPECS.forEach(s => {
      document.getElementById('sl-'+s.key).addEventListener('input', e => {
        params[s.key] = parseFloat(e.target.value);
        document.getElementById('val-'+s.key).textContent = fmt(s, params[s.key]);
        recompute();
      });
    });
    // 重参数只读展示
    const heavy = D[key].heavy_keys || [];
    document.getElementById('heavy-list').innerHTML = heavy.map(k =>
      `<div class="kv"><span>${k}</span><span>${D[key].params[k]}</span></div>`).join('')
      + `<div class="kv"><span>benchmark</span><span>${D[key].benchmark}</span></div>`
      + `<div class="kv"><span>state_rule</span><span>${v(D[key].state_rule)}</span></div>`;
  }

  function delta(cur, base, invert){
    if (cur===null||base===null||cur===undefined||base===undefined) return '';
    const d = cur - base;
    if (Math.abs(d) < 1e-9) return '<span class="delta flat">±0</span>';
    const good = invert ? d < 0 : d > 0;
    const sign = d > 0 ? '+' : '';
    const txt = Math.abs(d) < 1 ? sign+(d*100).toFixed(1)+'pp' : sign+d;
    return `<span class="delta ${good?'up':'down'}">${txt}</span>`;
  }

  function recompute(){
    const d = D[key], S = d.series, src = v(d.index_source);
    const r = BT.runAll(S, params, D[key].index_pre);
    const m = r.meta;
    const bm = baseline.meta;
    const gw = params.grade_window, cuts = params.grade_cuts;

    const baseGW = m.baseline_all ? m.baseline_all[gw] : null;
    const exVal = m.excess ? m.excess.vs_all : null;
    document.getElementById('lab-stat').innerHTML = `
      <div class="cell"><div class="lab">${t('ls_signal_days')}</div><div class="val">${m.signal_days}${delta(m.signal_days, bm.signal_days)}</div></div>
      <div class="cell"><div class="lab">${t('ls_valid')}</div><div class="val">${m.n_clusters_valid}${delta(m.n_clusters_valid, bm.n_clusters_valid)}</div></div>
      <div class="cell"><div class="lab">${t('ls_gradable')}</div><div class="val">${m.n_gradable}${delta(m.n_gradable, bm.n_gradable)}</div></div>
      <div class="cell"><div class="lab">${t('ls_hit')}</div><div class="val">${pct(m.hit_rate,0)}${delta(m.hit_rate, bm.hit_rate)}</div></div>
      <div class="cell"><div class="lab">${t('ls_hit_mid', gw, (cuts[1]*100).toFixed(0))}</div><div class="val">${m.hit_mid}${delta(m.hit_mid, bm.hit_mid)}</div></div>
      <div class="cell"><div class="lab">${t('ls_hit_big', (cuts[0]*100).toFixed(0))}</div><div class="val">${m.hit_big}${delta(m.hit_big, bm.hit_big)}</div></div>
      <div class="cell"><div class="lab">${t('ls_base')}</div><div class="val">${baseGW?pct(baseGW.p_mid,0):'—'}</div></div>
      <div class="cell"><div class="lab">${t('ls_excess')}</div><div class="val">${exVal===null?'—':(exVal>=0?'+':'')+(exVal*100).toFixed(0)+'pp'}${delta(exVal, bm.excess?bm.excess.vs_all:null)}</div></div>`;

    document.getElementById('lab-note').innerHTML = m.n_gradable < 3
      ? t('lab_few', m.n_gradable)
      : t('lab_now', m.n_gradable, pct(m.hit_rate,0), baseGW?pct(baseGW.p_mid,0):'—',
          exVal===null?'—':(exVal>=0?'+':'')+(exVal*100).toFixed(0)+'pp');

    charts.forEach(c => c.destroy && c.destroy());
    charts = [];
    const markers = HO.signalMarkers(r.signalDays, r.clusters, LC);
    const gap = S.index_close.findIndex(v => v !== null);
    const bands = gap>0 ? [{i0:0,i1:gap,color:'#5C8AE6',opacity:0.10,label:t('no_index')}] : [];

    charts.push(new CE.TimeSeriesChart(document.getElementById('lab-trend'), {
      title: t('lab_trend_title', src), subtitle:t('lab_trend_sub'),
      dates:S.dates, height:320,
      series:[{label:src,color:'#D9A441',data:S.index_close,width:1.5}],
      markers, bands, yFormat:v=>v.toFixed(0)}));

    charts.push(new CE.TimeSeriesChart(document.getElementById('lab-cond1'), {
      title:t('lab_c1', params.ma_period), subtitle:t('holds', pct(m.cond_rate.cond1,1)),
      dates:S.dates, height:210,
      series:[{label:t('index'),color:'#97A3BC',data:S.index_close,width:1.1},
              {label:'MA',color:'#D9A441',data:r.ma,width:1.8}],
      bands, yFormat:v=>v.toFixed(0)}));
    charts.push(new CE.TimeSeriesChart(document.getElementById('lab-cond2'), {
      title:t('lab_c2', (params.threshold*100).toFixed(1)), subtitle:t('holds', pct(m.cond_rate.cond2,1)),
      dates:S.dates, height:210,
      series:[{label:t('new_high_pct'),color:'#C64B37',data:S.high_ratio,width:1.3},
              {label:t('new_low_pct'),color:'#5C8AE6',data:S.low_ratio,width:1.3}],
      hlines:[{value:params.threshold,color:'#D9A441',label:t('threshold')}],
      yFormat:v=>(v*100).toFixed(1)+'%'}));
    charts.push(new CE.TimeSeriesChart(document.getElementById('lab-cond3'), {
      title:t('lab_c3', params.hl_ratio.toFixed(1)), subtitle:t('holds', pct(m.cond_rate.cond3,1)),
      dates:S.dates, height:210,
      series:[{label:t('new_high_n'),color:'#C64B37',data:S.new_high,type:'bar',opacity:0.75},
              {label:t('threshold'),color:'#5C8AE6',data:S.new_low.map(v=>v==null?null:v*params.hl_ratio),width:1.4,dash:'4,3'}],
      yFormat:v=>v.toFixed(0)}));
    charts.push(new CE.TimeSeriesChart(document.getElementById('lab-cond4'), {
      title:t('lab_c4'), subtitle:t('lab_c4_sub', pct(m.cond_rate.cond4,1)),
      dates:S.dates, height:210,
      series:[{label:'McClellan',color:'#E0904A',data:S.mcclellan,type:'bar'}],
      hlines:[{value:0,color:'#7d8aa0',label:t('zero_axis')}], yFormat:v=>v.toFixed(0)}));

    const groups = params.dd_windows.filter(w=>r.box[w]).map(w =>
      ({label:t('fwd', w), ...r.box[w], color:'#D9A441'}));
    if (groups.length) charts.push(new CE.BoxPlotChart(document.getElementById('lab-box'), {
      title:t('lab_box_title'), subtitle:t('lab_box_sub', m.n_gradable),
      groups, yFormat:v=>(v*100).toFixed(0)+'%'}));
    else document.getElementById('lab-box').innerHTML = '';

    document.getElementById('lab-tbody').innerHTML = r.clusters.length ? r.clusters.map(c => {
      const dd = c['dd'+gw];
      return `<tr><td class="num">${c.id}</td><td class="strong">${c.start} ~ ${c.end}</td>
        <td class="num">${c.count}</td>
        <td><span class="tag ${c.type==='有效集群'?'tag-amber':'tag-blue'}">${v(c.type)}</span></td>
        <td class="num">${dd===null||dd===undefined?t('pending_dd'):pct(dd,1)}</td>
        <td><span class="tag ${TAG[c.level]||'tag-blue'}">${v(c.level)}</span></td></tr>`;
    }).join('') : `<tr><td colspan="6" style="text-align:center;color:var(--text-faint)">${t('lab_no_signal')}</td></tr>`;
  }

  function switchTo(k){
    key = k;
    params = defaults();
    baseline = BT.runAll(D[key].series, D[key].params, D[key].index_pre);
    document.querySelectorAll('.toggle-btn').forEach(b => b.classList.toggle('active', b.dataset.cfg===k));
    document.getElementById('lab-caption').textContent =
      `${v(D[key].strategy)} · ${v(D[key].index_source)} · ${v(D[key].state_rule)}`;
    buildSliders();
    recompute();
  }

  document.querySelectorAll('.toggle-btn').forEach(b =>
    b.addEventListener('click', () => switchTo(b.dataset.cfg)));
  document.getElementById('btn-reset').addEventListener('click', () => {
    params = defaults(); buildSliders(); recompute();
  });

  switchTo(key);
})();
"""



def _load_payloads(out_dir: Path) -> dict:
    """读取目录下所有 export_*.json（包括 export_unadj.py 的对照组）。"""
    payloads = {}
    for p in sorted(out_dir.glob('export_*.json')):
        key = p.stem[len('export_'):]
        payloads[key] = json.loads(p.read_text(encoding='utf-8'))
    return payloads


if __name__ == '__main__':
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(config.OUT_DIR)
    payloads = _load_payloads(out_dir)
    if not payloads:
        raise SystemExit(
            f'{out_dir} 下没有 export_*.json, 先跑 `python hindenburg.py export` '
            f'(或 `python hindenburg.py export --out-dir {out_dir}`)')
    for lang in LANGS:
        path = build_lab(payloads, out_dir / page_name('lab', lang), lang)
        print(f'已生成 {path} ({path.stat().st_size/1024:.0f} KB)')
