/* ============================================================
   common.js —— 仪表盘和参数实验室共用的小工具，挂在 window.HO 上

     HO.TAG            回撤等级 → 徽标 CSS 类名
     HO.pct(x, d)      小数 → 百分比字符串（null 显示为 —）
     HO.signalMarkers  信号日 → 走势图标注点（文字来自 i18n.js）
   ============================================================ */
window.HO = (function () {
  const TAG = {'较大回撤':'tag-red','中型回调':'tag-orange','小幅回调':'tag-amber',
               '无显著回撤':'tag-teal','近期信号':'tag-blue'};

  const pct = (x,d) => (x===null||x===undefined) ? '—' : (x*100).toFixed(d===undefined?1:d)+'%';

  /* 每个信号日一个点，颜色按它自己的回撤等级；
     属于有效集群的画三角形，孤立的单点信号画小圆点。 */
  function signalMarkers(signalDays, clusters, LC) {
    const clusterById = {};
    clusters.filter(c => c.type==='有效集群').forEach(c => { clusterById[c.id] = c; });
    return (signalDays || []).map(sd => {
      const c = sd.cluster_id != null ? clusterById[sd.cluster_id] : null;
      return {index: sd.index, color: LC[sd.level]||'#5C8AE6',
        shape: c ? 'triangle' : 'dot', r: c ? 6 : 3,
        label: c ? I18N.t('mk_cluster', c.id, I18N.v(sd.level)) : I18N.t('mk_single', I18N.v(sd.level)),
        sublabel: c ? I18N.t('mk_range', c.start, c.end, c.count) : sd.date};
    });
  }

  return {TAG, pct, signalMarkers};
})();
