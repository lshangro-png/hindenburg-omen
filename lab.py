"""
参数实验室：生成 lab.html，在浏览器里拖滑块实时重算轻参数。

用法：
    python hindenburg.py export     # 先导出带中间序列的 JSON
    python lab.py                   # 读 output/export_*.json，生成 output/lab.html
    python lab.py <目录>            # 指定别的导出目录

原理：导出的 JSON 带着中间序列，assets/backtest.js 用和 Python 相同的逻辑重算
四条件、集群和回撤。重参数决定中间序列本身，浏览器里算不了，只做只读展示。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import config
from viz import _asset, _head, toggle_buttons, LEVEL_COLOR

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


def build_lab(payloads: dict, out_path: Path) -> Path:
    """payloads: {键: 导出的 JSON}，写出 lab.html。"""
    btns = toggle_buttons(payloads)

    html = _head('兴登堡预兆 · 参数实验室', LAB_CSS) + f"""
<nav class="topnav">
  <span class="brand">HINDENBURG·LAB</span>
  <a href="dashboard.html">← 回测仪表盘</a>
</nav>

<header class="hero" style="padding-bottom:24px">
  <div class="eyebrow"><span class="beacon"></span>实时重算 · 无需重跑数据</div>
  <h1>参数<em>实验室</em></h1>
  <p class="sub">拖动左侧滑块，四条件判定、集群划分、回撤评级会立刻重算——因为导出的数据里带着
  指数收盘、新高新低占比、家数、McClellan 值这些<b>中间序列</b>，四个条件都能由它们推出来。
  计算逻辑与 <code>hindenburg.py</code> 逐行对应（已验证结果完全一致）。</p>
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
    <h3>轻参数</h3>
    <p class="hint">这些只依赖已算好的中间序列，改动毫秒级生效。</p>
    <div id="sliders"></div>
    <div class="slider-row">
      <label>集群方式<b id="val-cm"></b></label>
      <select id="sel-cm" style="width:100%;padding:6px;border-radius:6px;background:var(--panel);
        color:var(--text);border:1px solid var(--line);font-size:12.5px">
        <option value="fixed">fixed · 窗口从首日固定</option>
        <option value="chain">chain · 链式延展</option>
      </select>
    </div>
    <div class="slider-row">
      <label>回撤口径<b id="val-dm"></b></label>
      <select id="sel-dm" style="width:100%;padding:6px;border-radius:6px;background:var(--panel);
        color:var(--text);border:1px solid var(--line);font-size:12.5px">
        <option value="peak_trough">peak_trough · 窗口内峰谷</option>
        <option value="from_signal">from_signal · 锚点日起最低点</option>
      </select>
    </div>
    <div class="lab-actions">
      <button id="btn-reset" type="button">恢复默认</button>
    </div>
    <div class="heavy-box">
      <h4>重参数（改这些必须重跑 Python）</h4>
      <div id="heavy-list"></div>
      <p class="note">这些参数决定了 McClellan 值、新高新低占比这些<b>输入序列本身</b>。
      浏览器里没有全市场股票面板，算不出来。要改就在 <code>config.py</code> 里改，然后重跑
      <code>python hindenburg.py clear-cache &amp;&amp; python hindenburg.py export</code>，
      再 <code>python lab.py</code> 重新生成本页。</p>
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
      <thead><tr><th>集群</th><th>预警区间</th><th>特征数</th><th>类型</th><th>跌幅</th><th>等级</th></tr></thead>
      <tbody id="lab-tbody"></tbody></table></div></div>
  </div>
</div>
</div>

<footer>
  <p><b>如何用</b>：先在这里把轻参数调顺（免费、实时），定下来之后再去 <code>config.py</code>
  改重参数并重跑。左上角"恢复默认"回到 config.py 里的设定值。</p>
  <p><b>注意</b>：调参极易过拟合。样本量本就很小，多试几组总能找到"看起来很准"的组合，
  但那多半是过拟合。</p>
</footer>

<script>{_asset('chart_engine.js')}</script>
<script>{_asset('backtest.js')}</script>
<script>{_asset('common.js')}</script>
<script>
window.__DATA__ = {json.dumps(payloads, ensure_ascii=False)};
window.__SLIDERS__ = {json.dumps(config.SLIDER_SPECS, ensure_ascii=False)};
window.__LEVEL_COLOR__ = {json.dumps(LEVEL_COLOR, ensure_ascii=False)};
</script>
<script>
{LAB_JS}
</script>
</body></html>"""

    out_path.write_text(html, encoding='utf-8')
    return out_path


LAB_JS = r"""
(function(){
  const D = window.__DATA__, SPECS = window.__SLIDERS__, LC = window.__LEVEL_COLOR__;
  const {TAG, pct} = window.HO;
  let key = Object.keys(D)[0];
  let params = null, baseline = null, charts = [];

  function defaults(){ return JSON.parse(JSON.stringify(D[key].params)); }

  function buildSliders(){
    const box = document.getElementById('sliders');
    box.innerHTML = SPECS.map(s => {
      const v = params[s.key], base = D[key].params[s.key];
      const disp = s.pct ? (v*100).toFixed(1)+'%' : v + (s.unit||'');
      const bdisp = s.pct ? (base*100).toFixed(1)+'%' : base + (s.unit||'');
      return `<div class="slider-row">
        <label>${s.label}<b id="val-${s.key}">${disp}</b></label>
        <input type="range" id="sl-${s.key}" min="${s.min}" max="${s.max}" step="${s.step}" value="${v}">
        <div class="base">默认 ${bdisp}</div></div>`;
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
        const disp = s.pct ? (params[s.key]*100).toFixed(1)+'%' : params[s.key] + (s.unit||'');
        document.getElementById('val-'+s.key).textContent = disp;
        recompute();
      });
    });
    // 重参数只读展示
    const heavy = D[key].heavy_keys || [];
    document.getElementById('heavy-list').innerHTML = heavy.map(k =>
      `<div class="kv"><span>${k}</span><span>${D[key].params[k]}</span></div>`).join('')
      + `<div class="kv"><span>benchmark</span><span>${D[key].benchmark}</span></div>`
      + `<div class="kv"><span>state_rule</span><span>${D[key].state_rule}</span></div>`;
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
    const d = D[key], S = d.series;
    const r = BT.runAll(S, params, D[key].index_pre);
    const m = r.meta;
    const bm = baseline.meta;
    const gw = params.grade_window, cuts = params.grade_cuts;

    const baseGW = m.baseline_all ? m.baseline_all[gw] : null;
    const exVal = m.excess ? m.excess.vs_all : null;
    document.getElementById('lab-stat').innerHTML = `
      <div class="cell"><div class="lab">信号天数</div><div class="val">${m.signal_days}${delta(m.signal_days, bm.signal_days)}</div></div>
      <div class="cell"><div class="lab">有效集群</div><div class="val">${m.n_clusters_valid}${delta(m.n_clusters_valid, bm.n_clusters_valid)}</div></div>
      <div class="cell"><div class="lab">可评估</div><div class="val">${m.n_gradable}${delta(m.n_gradable, bm.n_gradable)}</div></div>
      <div class="cell"><div class="lab">命中率</div><div class="val">${pct(m.hit_rate,0)}${delta(m.hit_rate, bm.hit_rate)}</div></div>
      <div class="cell"><div class="lab">中型以上命中(${gw}日≤${(cuts[1]*100).toFixed(0)}%)</div><div class="val">${m.hit_mid}${delta(m.hit_mid, bm.hit_mid)}</div></div>
      <div class="cell"><div class="lab">较大回撤命中(≤${(cuts[0]*100).toFixed(0)}%)</div><div class="val">${m.hit_big}${delta(m.hit_big, bm.hit_big)}</div></div>
      <div class="cell"><div class="lab">同期基准率(全样本随机一天)</div><div class="val">${baseGW?pct(baseGW.p_mid,0):'—'}</div></div>
      <div class="cell"><div class="lab">超额(命中率 - 基准率)</div><div class="val">${exVal===null?'—':(exVal>=0?'+':'')+(exVal*100).toFixed(0)+'pp'}${delta(exVal, bm.excess?bm.excess.vs_all:null)}</div></div>`;

    document.getElementById('lab-note').innerHTML = m.n_gradable < 3
      ? `<b>样本过少</b>：当前参数下只剩 ${m.n_gradable} 个可评估集群，命中率已经没有统计意义了。`
      : `<b>当前参数</b>：${m.n_gradable} 个可评估集群，中型回撤命中率 ${pct(m.hit_rate,0)}，` +
        `同期基准率 ${baseGW?pct(baseGW.p_mid,0):'—'}，超额 ${exVal===null?'—':(exVal>=0?'+':'')+(exVal*100).toFixed(0)+'pp'}。` +
        `样本量本就很小，命中率和基准率的差多半是噪声，不要只盯着命中率数字调参——` +
        `绿色/红色小字是相对 config.py 默认参数的变化。`;

    charts.forEach(c => c.destroy && c.destroy());
    charts = [];
    const markers = HO.signalMarkers(r.signalDays, r.clusters, LC);
    const gap = S.index_close.findIndex(v => v !== null);
    const bands = gap>0 ? [{i0:0,i1:gap,color:'#5C8AE6',opacity:0.10,label:'指数无数据'}] : [];

    charts.push(new CE.TimeSeriesChart(document.getElementById('lab-trend'), {
      title: d.index_source + ' 走势与当前参数下的信号', subtitle:'可滚轮缩放/拖拽',
      dates:S.dates, height:320,
      series:[{label:d.index_source,color:'#D9A441',data:S.index_close,width:1.5}],
      markers, bands, yFormat:v=>v.toFixed(0)}));

    charts.push(new CE.TimeSeriesChart(document.getElementById('lab-cond1'), {
      title:`条件① MA${params.ma_period} 上升`, subtitle:`触发率 ${pct(m.cond_rate.cond1,1)}`,
      dates:S.dates, height:210,
      series:[{label:'指数',color:'#97A3BC',data:S.index_close,width:1.1},
              {label:'MA',color:'#D9A441',data:r.ma,width:1.8}],
      bands, yFormat:v=>v.toFixed(0)}));
    charts.push(new CE.TimeSeriesChart(document.getElementById('lab-cond2'), {
      title:`条件② 占比 ≥ ${(params.threshold*100).toFixed(1)}%`, subtitle:`触发率 ${pct(m.cond_rate.cond2,1)}`,
      dates:S.dates, height:210,
      series:[{label:'新高占比',color:'#C64B37',data:S.high_ratio,width:1.3},
              {label:'新低占比',color:'#5C8AE6',data:S.low_ratio,width:1.3}],
      hlines:[{value:params.threshold,color:'#D9A441',label:'阈值'}],
      yFormat:v=>(v*100).toFixed(1)+'%'}));
    charts.push(new CE.TimeSeriesChart(document.getElementById('lab-cond3'), {
      title:`条件③ 新高 ≤ 新低×${params.hl_ratio.toFixed(1)}`, subtitle:`触发率 ${pct(m.cond_rate.cond3,1)}`,
      dates:S.dates, height:210,
      series:[{label:'新高家数',color:'#C64B37',data:S.new_high,type:'bar',opacity:0.75},
              {label:'阈值',color:'#5C8AE6',data:S.new_low.map(v=>v==null?null:v*params.hl_ratio),width:1.4,dash:'4,3'}],
      yFormat:v=>v.toFixed(0)}));
    charts.push(new CE.TimeSeriesChart(document.getElementById('lab-cond4'), {
      title:'条件④ McClellan < 0', subtitle:`触发率 ${pct(m.cond_rate.cond4,1)} · 该条件由重参数决定`,
      dates:S.dates, height:210,
      series:[{label:'McClellan',color:'#E0904A',data:S.mcclellan,type:'bar'}],
      hlines:[{value:0,color:'#7d8aa0',label:'0轴'}], yFormat:v=>v.toFixed(0)}));

    const groups = params.dd_windows.filter(w=>r.box[w]).map(w =>
      ({label:`未来${w}日`, ...r.box[w], color:'#D9A441'}));
    if (groups.length) charts.push(new CE.BoxPlotChart(document.getElementById('lab-box'), {
      title:'当前参数下的跌幅分布', subtitle:`基于 ${m.n_gradable} 个可评估集群`,
      groups, yFormat:v=>(v*100).toFixed(0)+'%'}));
    else document.getElementById('lab-box').innerHTML = '';

    document.getElementById('lab-tbody').innerHTML = r.clusters.length ? r.clusters.map(c => {
      const dd = c['dd'+gw];
      return `<tr><td class="num">${c.id}</td><td class="strong">${c.start} ~ ${c.end}</td>
        <td class="num">${c.count}</td>
        <td><span class="tag ${c.type==='有效集群'?'tag-amber':'tag-blue'}">${c.type}</span></td>
        <td class="num">${dd===null||dd===undefined?'观察中':pct(dd,1)}</td>
        <td><span class="tag ${TAG[c.level]||'tag-blue'}">${c.level}</span></td></tr>`;
    }).join('') : '<tr><td colspan="6" style="text-align:center;color:var(--text-faint)">当前参数下没有任何信号</td></tr>';
  }

  function switchTo(k){
    key = k;
    params = defaults();
    baseline = BT.runAll(D[key].series, D[key].params, D[key].index_pre);
    document.querySelectorAll('.toggle-btn').forEach(b => b.classList.toggle('active', b.dataset.cfg===k));
    document.getElementById('lab-caption').textContent =
      `${D[key].strategy} · ${D[key].index_source} · ${D[key].state_rule}`;
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
    path = build_lab(payloads, out_dir / 'lab.html')
    print(f'已生成 {path} ({path.stat().st_size/1024:.0f} KB)')
