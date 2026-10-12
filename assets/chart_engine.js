/* ============================================================
   chart-engine.js —— 纯原生 SVG 图表引擎, 零外部依赖
   支持: 多序列折线/面积/柱状混合, 标记点(带分级着色), 阈值参考线,
        滚轮缩放 + 拖拽平移(仅时间序列图), 悬浮提示, 响应式重绘
   ============================================================ */

// 界面文字：页面加载了 i18n.js 就按当前语言取，否则用中文
const CE_TEXT = { ce_reset: '重置视图', ce_hint: '滚轮缩放 · 拖拽平移', ce_max: '最大', ce_median: '中位', ce_min: '最小' };
const ceT = (k) => (window.I18N ? window.I18N.t(k) : CE_TEXT[k]);

window.CE = (() => {
  const SVGNS = 'http://www.w3.org/2000/svg';

  function el(tag, attrs = {}, parent = null) {
    const n = document.createElementNS(SVGNS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  function bisect(arr, x) {
    // arr 单调递增, 找最接近 x 的索引
    let lo = 0, hi = arr.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (arr[mid] < x) lo = mid + 1; else hi = mid;
    }
    if (lo > 0 && Math.abs(arr[lo - 1] - x) < Math.abs(arr[lo] - x)) return lo - 1;
    return lo;
  }

  function niceTicks(min, max, count) {
    if (min === max) { min -= 1; max += 1; }
    const span = max - min;
    const step0 = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const norm = step0 / mag;
    const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
    const start = Math.ceil(min / step) * step;
    const ticks = [];
    for (let v = start; v <= max + step * 1e-6; v += step) ticks.push(+v.toFixed(10));
    return ticks;
  }

  /* ---------------- 时间序列图 (折线/面积/柱状 混合, 支持缩放平移) --------------- */
  class TimeSeriesChart {
    constructor(container, opts) {
      this.root = typeof container === 'string' ? document.querySelector(container) : container;
      this.dates = opts.dates;
      this.n = this.dates.length;
      this.series = opts.series || [];
      this.markers = opts.markers || [];
      this.hlines = opts.hlines || [];
      this.bands = opts.bands || []; // [{i0,i1,color,label}] 区间高亮, 如数据缺口标注
      this.height = opts.height || 260;
      this.yFormat = opts.yFormat || (v => v.toFixed(2));
      this.yDomainFixed = opts.yDomain || null;
      this.yZeroBase = !!opts.yZeroBase;
      this.allowZoom = opts.allowZoom !== false;
      this.title = opts.title || '';
      this.subtitle = opts.subtitle || '';
      this.pad = { top: 14, right: 18, bottom: 26, left: 58 };
      this.view = { i0: 0, i1: this.n - 1 };
      this.minSpan = Math.max(20, Math.floor(this.n * 0.01));
      this._build();
      this._bind();
      this.render();
      this._ro = new ResizeObserver(() => this.render());
      this._ro.observe(this.root);
    }

    _build() {
      this.root.classList.add('ce-wrap');
      this.root.innerHTML = '';
      if (this.title) {
        const h = document.createElement('div');
        h.className = 'ce-title';
        h.innerHTML = `<span>${this.title}</span>` + (this.subtitle ? `<small>${this.subtitle}</small>` : '');
        this.root.appendChild(h);
      }
      const bar = document.createElement('div');
      bar.className = 'ce-toolbar';
      bar.innerHTML = `<button class="ce-btn ce-reset" type="button">${ceT('ce_reset')}</button>
        <span class="ce-hint">${ceT('ce_hint')}</span>`;
      this.root.appendChild(bar);
      bar.querySelector('.ce-reset').addEventListener('click', () => { this.view = { i0: 0, i1: this.n - 1 }; this.render(); });

      const wrap = document.createElement('div');
      wrap.className = 'ce-svgwrap';
      wrap.style.position = 'relative';
      this.svg = el('svg', { class: 'ce-svg', height: this.height });
      wrap.appendChild(this.svg);
      this.tooltip = document.createElement('div');
      this.tooltip.className = 'ce-tooltip';
      wrap.appendChild(this.tooltip);
      this.root.appendChild(wrap);
      this.wrap = wrap;

      this.gGrid = el('g', { class: 'ce-grid' }, this.svg);
      this.gBands = el('g', { class: 'ce-bands' }, this.svg);
      this.gHlines = el('g', { class: 'ce-hlines' }, this.svg);
      this.gSeries = el('g', { class: 'ce-series' }, this.svg);
      this.gMarkers = el('g', { class: 'ce-markers' }, this.svg);
      this.gAxis = el('g', { class: 'ce-axis' }, this.svg);
      this.crosshair = el('line', { class: 'ce-crosshair' }, this.svg);
      this.crosshair.style.display = 'none';
    }

    _bind() {
      if (!this.allowZoom) return;
      let dragging = false, lastX = 0;
      this.svg.addEventListener('wheel', (e) => {
        e.preventDefault();
        const rect = this.svg.getBoundingClientRect();
        const px = e.clientX - rect.left;
        const factor = e.deltaY > 0 ? 1.15 : 1 / 1.15;
        this._zoomAt(px, factor);
      }, { passive: false });
      this.svg.addEventListener('pointerdown', (e) => {
        dragging = true; lastX = e.clientX; this.svg.setPointerCapture(e.pointerId);
        this.svg.classList.add('ce-dragging');
      });
      this.svg.addEventListener('pointermove', (e) => {
        if (dragging) {
          const dx = e.clientX - lastX; lastX = e.clientX;
          this._panByPx(dx);
        }
        this._hoverAt(e);
      });
      // window 级别的监听器必须存下引用, 在 destroy() 里显式移除 —— 不然
      // 每次切换策略新建的图表都会往 window 上叠加一个, 旧监听器的闭包里
      // 攥着已经从 DOM 里删掉的旧 this.svg, 整个旧图表对象(包括它几千个
      // 点的 series/dates 数组)因此一直被引用、永远回收不掉。切换几次
      // 下来内存越堆越多, 页面也就越来越卡 —— 这正是切换延迟的根因。
      this._onPointerUp = () => { dragging = false; this.svg.classList.remove('ce-dragging'); };
      window.addEventListener('pointerup', this._onPointerUp);
      this.svg.addEventListener('pointerleave', () => { this.crosshair.style.display = 'none'; this.tooltip.style.opacity = 0; });
    }

    _innerW() { return Math.max(50, this.svg.clientWidth - this.pad.left - this.pad.right); }

    _zoomAt(px, factor) {
      const { i0, i1 } = this.view;
      const w = this._innerW();
      const frac = Math.min(1, Math.max(0, (px - this.pad.left) / w));
      const centerIdx = i0 + frac * (i1 - i0);
      let span = (i1 - i0) * factor;
      span = Math.min(this.n - 1, Math.max(this.minSpan, span));
      let newI0 = centerIdx - frac * span;
      let newI1 = newI0 + span;
      if (newI0 < 0) { newI1 -= newI0; newI0 = 0; }
      if (newI1 > this.n - 1) { newI0 -= (newI1 - (this.n - 1)); newI1 = this.n - 1; }
      newI0 = Math.max(0, newI0);
      this.view = { i0: newI0, i1: newI1 };
      this.render();
    }

    _panByPx(dxPx) {
      const { i0, i1 } = this.view;
      const w = this._innerW();
      const span = i1 - i0;
      let didx = -dxPx / w * span;
      let newI0 = i0 + didx, newI1 = i1 + didx;
      if (newI0 < 0) { newI1 -= newI0; newI0 = 0; }
      if (newI1 > this.n - 1) { newI0 -= (newI1 - (this.n - 1)); newI1 = this.n - 1; }
      this.view = { i0: Math.max(0, newI0), i1: Math.min(this.n - 1, newI1) };
      this.render();
    }

    _hoverAt(e) {
      const rect = this.svg.getBoundingClientRect();
      const px = e.clientX - rect.left, py = e.clientY - rect.top;
      const w = this._innerW();
      if (px < this.pad.left || px > this.pad.left + w) { this.crosshair.style.display = 'none'; this.tooltip.style.opacity = 0; return; }
      const { i0, i1 } = this.view;
      const frac = (px - this.pad.left) / w;
      const idxF = i0 + frac * (i1 - i0);
      const idx = Math.round(idxF);
      const x = this._x(idx);
      this.crosshair.setAttribute('x1', x); this.crosshair.setAttribute('x2', x);
      this.crosshair.setAttribute('y1', this.pad.top); this.crosshair.setAttribute('y2', this.height - this.pad.bottom);
      this.crosshair.style.display = 'block';

      let html = `<div class="ce-tt-date">${this.dates[idx]}</div>`;
      this.series.forEach(s => {
        const v = s.data[idx];
        if (v === null || v === undefined || Number.isNaN(v)) return;
        html += `<div class="ce-tt-row"><span class="ce-tt-dot" style="background:${s.color}"></span>${s.label}: <b>${(s.fmt || this.yFormat)(v)}</b></div>`;
      });
      const nearMarker = this.markers.find(m => Math.abs(m.index - idx) <= Math.max(1, (i1 - i0) / w * 4));
      if (nearMarker) {
        html += `<div class="ce-tt-marker" style="border-color:${nearMarker.color}"><b style="color:${nearMarker.color}">${nearMarker.label}</b>${nearMarker.sublabel ? '<br>' + nearMarker.sublabel : ''}</div>`;
      }
      this.tooltip.innerHTML = html;
      this.tooltip.style.opacity = 1;
      const ttW = this.tooltip.offsetWidth;
      let left = px + 14;
      if (left + ttW > this.wrap.clientWidth) left = px - ttW - 14;
      this.tooltip.style.left = left + 'px';
      this.tooltip.style.top = Math.max(4, py - 10) + 'px';
    }

    _x(idx) {
      const { i0, i1 } = this.view;
      const w = this._innerW();
      return this.pad.left + (idx - i0) / (i1 - i0) * w;
    }
    _y(v, yMin, yMax) {
      const h = this.height - this.pad.top - this.pad.bottom;
      return this.pad.top + (1 - (v - yMin) / (yMax - yMin)) * h;
    }

    render() {
      const w = this._innerW();
      this.svg.setAttribute('width', '100%');
      this.svg.setAttribute('viewBox', `0 0 ${w + this.pad.left + this.pad.right} ${this.height}`);
      this.svg.setAttribute('preserveAspectRatio', 'none');

      const i0 = Math.max(0, Math.floor(this.view.i0)), i1 = Math.min(this.n - 1, Math.ceil(this.view.i1));

      // y 定义域: 固定或按可见区间自动
      let yMin = Infinity, yMax = -Infinity;
      if (this.yDomainFixed) { [yMin, yMax] = this.yDomainFixed; }
      else {
        this.series.forEach(s => {
          for (let i = i0; i <= i1; i++) {
            const v = s.data[i];
            if (v === null || v === undefined || Number.isNaN(v)) continue;
            if (v < yMin) yMin = v; if (v > yMax) yMax = v;
          }
        });
        this.hlines.forEach(h => { if (h.value < yMin) yMin = h.value; if (h.value > yMax) yMax = h.value; });
        if (this.yZeroBase) yMin = Math.min(0, yMin);
        if (!isFinite(yMin) || !isFinite(yMax)) { yMin = 0; yMax = 1; }
        const pad = (yMax - yMin) * 0.08 || 1;
        yMin -= pad; yMax += pad;
      }
      this._yMin = yMin; this._yMax = yMax;

      this._drawGrid(yMin, yMax);
      this._drawBands();
      this._drawHlines(yMin, yMax);
      this._drawSeries(yMin, yMax, i0, i1);
      this._drawMarkers(yMin, yMax, i0, i1);
    }

    _drawBands() {
      this.gBands.innerHTML = '';
      const { i0, i1 } = this.view;
      this.bands.forEach(b => {
        const bi0 = Math.max(b.i0, i0), bi1 = Math.min(b.i1, i1);
        if (bi1 <= bi0) return;
        const x0 = this._x(bi0), x1 = this._x(bi1);
        el('rect', {
          x: x0, y: this.pad.top, width: Math.max(0.5, x1 - x0), height: this.height - this.pad.top - this.pad.bottom,
          fill: b.color || '#5C8AE6', opacity: b.opacity ?? 0.08,
        }, this.gBands);
        if (b.label && x1 - x0 > 40) {
          el('text', { x: (x0 + x1) / 2, y: this.pad.top + 13, 'text-anchor': 'middle', class: 'ce-bandlabel', fill: b.color || '#5C8AE6' }, this.gBands)
            .textContent = b.label;
        }
      });
    }

    _drawGrid(yMin, yMax) {
      this.gGrid.innerHTML = ''; this.gAxis.innerHTML = '';
      const w = this._innerW();
      const ticks = niceTicks(yMin, yMax, 5);
      ticks.forEach(t => {
        const y = this._y(t, yMin, yMax);
        el('line', { class: 'ce-gridline', x1: this.pad.left, x2: this.pad.left + w, y1: y, y2: y }, this.gGrid);
        el('text', { class: 'ce-ytick', x: this.pad.left - 8, y: y + 4, 'text-anchor': 'end' }, this.gAxis).textContent = this.yFormat(t);
      });
      // x 轴: 按可见范围抽取若干年/月标签
      const { i0, i1 } = this.view;
      const span = i1 - i0;
      const nLabels = Math.min(8, Math.max(3, Math.floor(w / 90)));
      for (let k = 0; k <= nLabels; k++) {
        const idx = Math.round(i0 + (span * k) / nLabels);
        if (idx < 0 || idx >= this.n) continue;
        const x = this._x(idx);
        el('line', { class: 'ce-tickline', x1: x, x2: x, y1: this.height - this.pad.bottom, y2: this.height - this.pad.bottom + 4 }, this.gAxis);
        el('text', { class: 'ce-xtick', x, y: this.height - this.pad.bottom + 17, 'text-anchor': 'middle' }, this.gAxis)
          .textContent = this.dates[idx].slice(0, 7);
      }
      el('line', { class: 'ce-axisline', x1: this.pad.left, x2: this.pad.left + w, y1: this.height - this.pad.bottom, y2: this.height - this.pad.bottom }, this.gAxis);
    }

    _drawHlines(yMin, yMax) {
      this.gHlines.innerHTML = '';
      const w = this._innerW();
      this.hlines.forEach(h => {
        const y = this._y(h.value, yMin, yMax);
        el('line', { class: 'ce-hline', x1: this.pad.left, x2: this.pad.left + w, y1: y, y2: y, stroke: h.color, 'stroke-dasharray': h.dash || '5,4' }, this.gHlines);
        if (h.label) el('text', { class: 'ce-hlabel', x: this.pad.left + w - 4, y: y - 5, 'text-anchor': 'end', fill: h.color }, this.gHlines).textContent = h.label;
      });
    }

    _drawSeries(yMin, yMax, i0, i1) {
      this.gSeries.innerHTML = '';
      const bars = this.series.filter(s => s.type === 'bar');
      const lines = this.series.filter(s => s.type !== 'bar');
      const w = this._innerW();
      const barW = Math.max(0.6, (w / (i1 - i0 + 1)) * 0.68);

      bars.forEach(s => {
        const g = el('g', { fill: s.color, opacity: s.opacity || 0.85 }, this.gSeries);
        for (let i = i0; i <= i1; i++) {
          const v = s.data[i];
          if (v === null || v === undefined || Number.isNaN(v)) continue;
          const x = this._x(i);
          const y0 = this._y(Math.max(0, s.base ?? 0), yMin, yMax);
          const y1 = this._y(v, yMin, yMax);
          const y = Math.min(y0, y1), hgt = Math.abs(y1 - y0);
          el('rect', { x: x - barW / 2, y, width: barW, height: Math.max(0.5, hgt) }, g);
        }
      });

      lines.forEach(s => {
        let d = '';
        let pen = false;
        const pts = [];
        for (let i = i0; i <= i1; i++) {
          const v = s.data[i];
          if (v === null || v === undefined || Number.isNaN(v)) { pen = false; continue; }
          const x = this._x(i), y = this._y(v, yMin, yMax);
          d += (pen ? 'L' : 'M') + x.toFixed(1) + ',' + y.toFixed(1) + ' ';
          pen = true;
          pts.push([x, y, v]);
        }
        if (s.area) {
          const yBase = this._y(s.base ?? yMin, yMin, yMax);
          let ad = '';
          let penA = false;
          for (let i = i0; i <= i1; i++) {
            const v = s.data[i];
            if (v === null || v === undefined || Number.isNaN(v)) { penA = false; continue; }
            const x = this._x(i), y = this._y(v, yMin, yMax);
            if (!penA) { ad += `M${x.toFixed(1)},${yBase.toFixed(1)} L${x.toFixed(1)},${y.toFixed(1)} `; penA = true; }
            else ad += `L${x.toFixed(1)},${y.toFixed(1)} `;
          }
          if (ad) el('path', { d: ad + `L${pts.length ? pts[pts.length - 1][0].toFixed(1) : 0},${yBase.toFixed(1)} Z`, fill: s.color, opacity: s.fillOpacity ?? 0.12, stroke: 'none' }, this.gSeries);
        }
        el('path', { d, fill: 'none', stroke: s.color, 'stroke-width': s.width || 1.6, 'stroke-dasharray': s.dash || '', 'stroke-linejoin': 'round' }, this.gSeries);
      });
    }

    _drawMarkers(yMin, yMax, i0, i1) {
      this.gMarkers.innerHTML = '';
      const anchorSeries = this.series[0];
      this.markers.forEach(m => {
        if (m.index < i0 || m.index > i1) return;
        const x = this._x(m.index);
        const v = m.y !== undefined ? m.y : (anchorSeries ? anchorSeries.data[m.index] : null);
        if (v === null || v === undefined || Number.isNaN(v)) return;
        const y = this._y(v, yMin, yMax);
        const shape = m.shape || 'circle';
        const g = el('g', { class: 'ce-marker' }, this.gMarkers);
        if (shape === 'triangle') {
          const r = m.r || 6;
          el('path', { d: `M${x},${y - r} L${x + r},${y + r} L${x - r},${y + r} Z`, fill: m.color, stroke: '#FFFFFF', 'stroke-width': 1 }, g);
        } else {
          el('circle', { cx: x, cy: y, r: m.r || 4.5, fill: m.color, stroke: '#FFFFFF', 'stroke-width': 1 }, g);
        }
      });
    }
    destroy() {
      if (this._ro) this._ro.disconnect();
      if (this._onPointerUp) window.removeEventListener('pointerup', this._onPointerUp);
    }
  }

  /* ---------------- 双泳道时间线对比 (集群区间 + 匹配连线) --------------- */
  class TimelineCompare {
    constructor(container, opts) {
      this.root = typeof container === 'string' ? document.querySelector(container) : container;
      this.dates = opts.dates;
      this.n = this.dates.length;
      this.lanes = opts.lanes; // [{label, color, items:[{id,i0,i1,level,color,label,sublabel}]}]
      this.links = opts.links || []; // [[laneIdx,itemId,laneIdx,itemId]]
      this.title = opts.title || ''; this.subtitle = opts.subtitle || '';
      this.laneH = opts.laneH || 46;
      this.pad = { top: 14, right: 18, bottom: 26, left: 118 };
      this.height = this.pad.top + this.pad.bottom + this.lanes.length * this.laneH + (this.lanes.length - 1) * 10;
      this.view = { i0: 0, i1: this.n - 1 };
      this.minSpan = Math.max(20, Math.floor(this.n * 0.02));
      this._build(); this._bind(); this.render();
      this._ro = new ResizeObserver(() => this.render());
      this._ro.observe(this.root);
    }
    _build() {
      this.root.classList.add('ce-wrap'); this.root.innerHTML = '';
      if (this.title) {
        const h = document.createElement('div'); h.className = 'ce-title';
        h.innerHTML = `<span>${this.title}</span>` + (this.subtitle ? `<small>${this.subtitle}</small>` : '');
        this.root.appendChild(h);
      }
      const bar = document.createElement('div'); bar.className = 'ce-toolbar';
      bar.innerHTML = `<button class="ce-btn ce-reset" type="button">${ceT('ce_reset')}</button><span class="ce-hint">${ceT('ce_hint')}</span>`;
      this.root.appendChild(bar);
      bar.querySelector('.ce-reset').addEventListener('click', () => { this.view = { i0: 0, i1: this.n - 1 }; this.render(); });
      const wrap = document.createElement('div'); wrap.className = 'ce-svgwrap'; wrap.style.position = 'relative';
      this.svg = el('svg', { class: 'ce-svg', height: this.height }); wrap.appendChild(this.svg);
      this.tooltip = document.createElement('div'); this.tooltip.className = 'ce-tooltip'; wrap.appendChild(this.tooltip);
      this.root.appendChild(wrap); this.wrap = wrap;
    }
    _bind() {
      let dragging = false, lastX = 0;
      this.svg.addEventListener('wheel', (e) => {
        e.preventDefault();
        const rect = this.svg.getBoundingClientRect();
        this._zoomAt(e.clientX - rect.left, e.deltaY > 0 ? 1.15 : 1 / 1.15);
      }, { passive: false });
      this.svg.addEventListener('pointerdown', (e) => { dragging = true; lastX = e.clientX; this.svg.setPointerCapture(e.pointerId); this.svg.classList.add('ce-dragging'); });
      this.svg.addEventListener('pointermove', (e) => { if (dragging) { const dx = e.clientX - lastX; lastX = e.clientX; this._panByPx(dx); } });
      this._onPointerUp = () => { dragging = false; this.svg.classList.remove('ce-dragging'); };
      window.addEventListener('pointerup', this._onPointerUp);
    }
    _innerW() { return Math.max(50, this.svg.clientWidth) - this.pad.left - this.pad.right; }
    _zoomAt(px, factor) {
      const { i0, i1 } = this.view, w = this._innerW();
      const frac = Math.min(1, Math.max(0, (px - this.pad.left) / w));
      const c = i0 + frac * (i1 - i0);
      let span = Math.min(this.n - 1, Math.max(this.minSpan, (i1 - i0) * factor));
      let ni0 = c - frac * span, ni1 = ni0 + span;
      if (ni0 < 0) { ni1 -= ni0; ni0 = 0; } if (ni1 > this.n - 1) { ni0 -= (ni1 - (this.n - 1)); ni1 = this.n - 1; }
      this.view = { i0: Math.max(0, ni0), i1: Math.min(this.n - 1, ni1) }; this.render();
    }
    _panByPx(dxPx) {
      const { i0, i1 } = this.view, w = this._innerW(), span = i1 - i0;
      let didx = -dxPx / w * span, ni0 = i0 + didx, ni1 = i1 + didx;
      if (ni0 < 0) { ni1 -= ni0; ni0 = 0; } if (ni1 > this.n - 1) { ni0 -= (ni1 - (this.n - 1)); ni1 = this.n - 1; }
      this.view = { i0: Math.max(0, ni0), i1: Math.min(this.n - 1, ni1) }; this.render();
    }
    _x(idx) { const { i0, i1 } = this.view, w = this._innerW(); return this.pad.left + (idx - i0) / (i1 - i0) * w; }
    _laneY(li) { return this.pad.top + li * (this.laneH + 10); }
    _moveTip(e) {
      const rect = this.wrap.getBoundingClientRect();
      let left = e.clientX - rect.left + 12; const ttW = this.tooltip.offsetWidth;
      if (left + ttW > this.wrap.clientWidth) left = e.clientX - rect.left - ttW - 12;
      this.tooltip.style.left = left + 'px'; this.tooltip.style.top = (e.clientY - rect.top - 10) + 'px';
    }
    render() {
      const w = this._innerW();
      this.svg.innerHTML = '';
      this.svg.setAttribute('viewBox', `0 0 ${w + this.pad.left + this.pad.right} ${this.height}`);
      this.svg.setAttribute('preserveAspectRatio', 'none');
      const { i0, i1 } = this.view;

      // 泳道背景 + 标签
      this.lanes.forEach((lane, li) => {
        const y = this._laneY(li);
        el('rect', { x: this.pad.left, y, width: w, height: this.laneH, fill: '#E4E8F2', opacity: .6, rx: 4 }, this.svg);
        el('text', { x: this.pad.left - 10, y: y + this.laneH / 2 + 4, 'text-anchor': 'end', class: 'ce-lanelabel', fill: lane.color }, this.svg)
          .textContent = lane.label;
      });

      // 连线 (先画, 压在条形下面)
      const posOf = (li, id) => {
        const lane = this.lanes[li]; const item = lane.items.find(it => it.id === id);
        if (!item) return null;
        const cx = (this._x(item.i0) + this._x(item.i1)) / 2;
        return { x: cx, y: this._laneY(li) + this.laneH / 2 };
      };
      this.links.forEach(([l1, id1, l2, id2]) => {
        const p1 = posOf(l1, id1), p2 = posOf(l2, id2);
        if (!p1 || !p2) return;
        el('path', { d: `M${p1.x},${p1.y} C${p1.x},${(p1.y + p2.y) / 2} ${p2.x},${(p1.y + p2.y) / 2} ${p2.x},${p2.y}`,
          fill: 'none', stroke: '#8B93A7', 'stroke-width': 1.2, 'stroke-dasharray': '3,3', opacity: .7 }, this.svg);
      });

      // 条形
      this.lanes.forEach((lane, li) => {
        const y = this._laneY(li);
        lane.items.forEach(it => {
          if (it.i1 < i0 || it.i0 > i1) return;
          const x0 = this._x(Math.max(it.i0, i0)), x1 = this._x(Math.min(it.i1, i1));
          const bw = Math.max(6, x1 - x0);
          const g = el('g', { class: 'ce-marker' }, this.svg);
          el('rect', { x: x0, y: y + 6, width: bw, height: this.laneH - 12, fill: it.color, opacity: .85, rx: 4 }, g);
          g.addEventListener('pointerenter', (e) => {
            this.tooltip.innerHTML = `<div class="ce-tt-date">${it.label}</div><div class="ce-tt-row">${it.sublabel || ''}</div>`;
            this.tooltip.style.opacity = 1; this._moveTip(e);
          });
          g.addEventListener('pointermove', (e) => this._moveTip(e));
          g.addEventListener('pointerleave', () => this.tooltip.style.opacity = 0);
        });
      });

      // x 轴年份刻度
      const span = i1 - i0;
      const nLabels = Math.min(10, Math.max(3, Math.floor(w / 80)));
      const axisY = this.pad.top + this.lanes.length * (this.laneH + 10) - 10 + 14;
      for (let k = 0; k <= nLabels; k++) {
        const idx = Math.round(i0 + span * k / nLabels);
        if (idx < 0 || idx >= this.n) continue;
        const x = this._x(idx);
        el('text', { x, y: axisY, 'text-anchor': 'middle', class: 'ce-xtick' }, this.svg).textContent = this.dates[idx].slice(0, 7);
      }
    }
    destroy() {
      if (this._ro) this._ro.disconnect();
      if (this._onPointerUp) window.removeEventListener('pointerup', this._onPointerUp);
    }
  }

  /* ---------------- 箱线图 --------------- */
  class BoxPlotChart {
    constructor(container, opts) {
      this.root = typeof container === 'string' ? document.querySelector(container) : container;
      this.groups = opts.groups; // [{label, min,q1,median,q3,max,n,color}]
      this.height = opts.height || 260;
      this.title = opts.title || ''; this.subtitle = opts.subtitle || '';
      this.yFormat = opts.yFormat || (v => (v * 100).toFixed(0) + '%');
      this.pad = { top: 16, right: 20, bottom: 36, left: 54 };
      this._build(); this.render();
      this._ro = new ResizeObserver(() => this.render());
      this._ro.observe(this.root);
    }
    _build() {
      this.root.classList.add('ce-wrap'); this.root.innerHTML = '';
      if (this.title) {
        const h = document.createElement('div'); h.className = 'ce-title';
        h.innerHTML = `<span>${this.title}</span>` + (this.subtitle ? `<small>${this.subtitle}</small>` : '');
        this.root.appendChild(h);
      }
      const wrap = document.createElement('div'); wrap.className = 'ce-svgwrap'; wrap.style.position = 'relative';
      this.svg = el('svg', { class: 'ce-svg', height: this.height }); wrap.appendChild(this.svg);
      this.tooltip = document.createElement('div'); this.tooltip.className = 'ce-tooltip'; wrap.appendChild(this.tooltip);
      this.root.appendChild(wrap); this.wrap = wrap;
    }
    render() {
      const w = Math.max(50, this.svg.clientWidth) - this.pad.left - this.pad.right;
      this.svg.innerHTML = '';
      this.svg.setAttribute('viewBox', `0 0 ${w + this.pad.left + this.pad.right} ${this.height}`);
      this.svg.setAttribute('preserveAspectRatio', 'none');
      let yMin = 0, yMax = -Infinity;
      this.groups.forEach(g => { if (g.min < yMin) yMin = g.min; if (g.max > yMax) yMax = g.max; });
      const padY = (yMax - yMin) * 0.12 || 0.02;
      yMin -= padY; yMax += padY;
      const h = this.height - this.pad.top - this.pad.bottom;
      const yOf = v => this.pad.top + (1 - (v - yMin) / (yMax - yMin)) * h;

      const ticks = niceTicksPct(yMin, yMax);
      ticks.forEach(t => {
        const y = yOf(t);
        el('line', { class: 'ce-gridline', x1: this.pad.left, x2: this.pad.left + w, y1: y, y2: y }, this.svg);
        el('text', { class: 'ce-ytick', x: this.pad.left - 8, y: y + 4, 'text-anchor': 'end' }, this.svg).textContent = this.yFormat(t);
      });
      el('line', { class: 'ce-hline', x1: this.pad.left, x2: this.pad.left + w, y1: yOf(0), y2: yOf(0), stroke: '#9AA2B5', 'stroke-dasharray': '3,4' }, this.svg);

      const n = this.groups.length;
      const slot = w / n;
      const boxW = Math.min(56, slot * 0.42);
      this.groups.forEach((g, i) => {
        const cx = this.pad.left + slot * (i + 0.5);
        const color = g.color || '#d9a441';
        const grp = el('g', {}, this.svg);
        el('line', { x1: cx, x2: cx, y1: yOf(g.min), y2: yOf(g.max), stroke: color, 'stroke-width': 1.4 }, grp);
        el('line', { x1: cx - boxW * 0.28, x2: cx + boxW * 0.28, y1: yOf(g.min), y2: yOf(g.min), stroke: color, 'stroke-width': 1.4 }, grp);
        el('line', { x1: cx - boxW * 0.28, x2: cx + boxW * 0.28, y1: yOf(g.max), y2: yOf(g.max), stroke: color, 'stroke-width': 1.4 }, grp);
        const boxY = Math.min(yOf(g.q1), yOf(g.q3));
        const boxH = Math.abs(yOf(g.q1) - yOf(g.q3));
        el('rect', { x: cx - boxW / 2, y: boxY, width: boxW, height: Math.max(1, boxH), fill: color, opacity: 0.22, stroke: color, 'stroke-width': 1.4, rx: 3 }, grp);
        el('line', { x1: cx - boxW / 2, x2: cx + boxW / 2, y1: yOf(g.median), y2: yOf(g.median), stroke: color, 'stroke-width': 2.2 }, grp);
        grp.style.cursor = 'pointer';
        grp.addEventListener('pointerenter', (e) => {
          this.tooltip.innerHTML = `<div class="ce-tt-date">${g.label} (n=${g.n})</div>
            <div class="ce-tt-row">${ceT('ce_max')}: <b>${this.yFormat(g.max)}</b></div>
            <div class="ce-tt-row">Q3: <b>${this.yFormat(g.q3)}</b></div>
            <div class="ce-tt-row">${ceT('ce_median')}: <b>${this.yFormat(g.median)}</b></div>
            <div class="ce-tt-row">Q1: <b>${this.yFormat(g.q1)}</b></div>
            <div class="ce-tt-row">${ceT('ce_min')}: <b>${this.yFormat(g.min)}</b></div>`;
          this.tooltip.style.opacity = 1; this._moveTip(e);
        });
        grp.addEventListener('pointermove', (e) => this._moveTip(e));
        grp.addEventListener('pointerleave', () => this.tooltip.style.opacity = 0);

        el('text', { class: 'ce-xtick', x: cx, y: this.height - this.pad.bottom + 17, 'text-anchor': 'middle' }, this.svg).textContent = g.label;
      });
    }
    _moveTip(e) {
      const rect = this.wrap.getBoundingClientRect();
      let left = e.clientX - rect.left + 12;
      const ttW = this.tooltip.offsetWidth;
      if (left + ttW > this.wrap.clientWidth) left = e.clientX - rect.left - ttW - 12;
      this.tooltip.style.left = left + 'px';
      this.tooltip.style.top = (e.clientY - rect.top - 10) + 'px';
    }
    destroy() { if (this._ro) this._ro.disconnect(); }
  }
  function niceTicksPct(min, max) { return niceTicks(min, max, 5); }

  return { TimeSeriesChart, BoxPlotChart, TimelineCompare, el, bisect };
})();
