/* Chart.js helpers: theme-aware defaults, a registry that re-renders on theme
   change, category color binding, HTML legends, donut center plugin. */
const charts = (() => {
  const registry = new Map(); // canvas -> {chart, build}

  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function theme() {
    return {
      text: css('--text-3'), text1: css('--text-1'), text2: css('--text-2'), grid: css('--chart-grid'), axis: css('--chart-axis'),
      surface: css('--surface'), tipBg: css('--surface-overlay'), tipBorder: css('--border-strong'), accent: css('--accent'),
      muted: css('--chart-muted'), success: css('--success'), danger: css('--danger'),
      reduced: window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches,
    };
  }

  function applyChartDefaults() {
    if (typeof Chart === 'undefined') return;
    const t = theme(); const d = Chart.defaults;
    d.font.family = "'Inter', system-ui, sans-serif"; d.font.size = 12; d.color = t.text;
    d.animation.duration = t.reduced ? 0 : 400;
    d.plugins.legend.display = false;
    Object.assign(d.plugins.tooltip, {
      backgroundColor: t.tipBg, titleColor: t.text1, bodyColor: t.text2, borderColor: t.tipBorder, borderWidth: 1,
      padding: 10, cornerRadius: 8, displayColors: true, boxWidth: 8, boxHeight: 8, boxPadding: 4, usePointStyle: true,
      mode: 'index', intersect: false, titleFont: { weight: '600' },
    });
    d.scale.grid.color = t.grid; d.scale.border.color = t.axis; d.scale.ticks.padding = 8;
    d.elements.bar.borderRadius = 4; d.elements.bar.borderSkipped = 'start';
    d.elements.line.borderWidth = 2; d.elements.line.tension = 0.3;
    d.elements.point.radius = 0; d.elements.point.hoverRadius = 5; d.elements.point.hitRadius = 12;
    d.elements.arc.borderWidth = 0;
    d.maintainAspectRatio = false; d.responsive = true;
  }

  /* catColor('c4') or catColor({color:'c4'}) -> hex for the current theme. */
  function catColor(x) {
    const slot = typeof x === 'string' ? x : (x && x.color);
    if (!slot) return css('--chart-muted');
    const v = css(`--${slot}`);
    return v || css('--chart-muted');
  }
  function withAlpha(hex, a) {
    const h = hex.replace('#', '');
    const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  function gradientFill(ctx, hex, { from = 0.18, to = 0 } = {}) {
    const h = ctx.canvas.height || 260;
    const g = ctx.createLinearGradient(0, 0, 0, h);
    g.addColorStop(0, withAlpha(hex, from)); g.addColorStop(1, withAlpha(hex, to));
    return g;
  }
  function currencyTicks(currency = 'USD') {
    return { callback: (v) => fmtMoney(v, currency, { compact: true }), maxTicksLimit: 5 };
  }
  function currencyTooltip(currency = 'USD') {
    return { label: (c) => `${c.dataset.label ? c.dataset.label + ': ' : ''}${fmtMoney(c.parsed.y ?? c.parsed, currency)}` };
  }

  /* makeChart(canvas, (theme) => config). Registered charts rebuild on theme change. */
  /* Canvases replaced by innerHTML re-renders leave Chart instances behind; drop them. */
  function prune() {
    registry.forEach((r, c) => { if (!c.isConnected) { r.chart.destroy(); registry.delete(c); } });
  }
  function makeChart(canvas, build) {
    if (typeof Chart === 'undefined') return null;
    applyChartDefaults();
    prune();
    destroyChart(canvas);
    const chart = new Chart(canvas.getContext('2d'), build(theme()));
    registry.set(canvas, { chart, build });
    return chart;
  }
  function destroyChart(canvas) {
    const r = registry.get(canvas);
    if (r) { r.chart.destroy(); registry.delete(canvas); }
  }
  function rerenderAll() {
    applyChartDefaults();
    prune();
    registry.forEach(({ chart, build }) => {
      const cfg = build(theme());
      chart.data = cfg.data;
      chart.options = cfg.options || {};
      chart.update('none');
    });
  }
  window.addEventListener('ispend:theme', rerenderAll);

  /* HTML legend: container gets .legend-item buttons; toggling hides datasets (bar/line) or slices (doughnut). */
  function htmlLegend(container, chart, { onClick, values, currency = 'USD', list = false } = {}) {
    const isPie = chart.config.type === 'doughnut' || chart.config.type === 'pie';
    const items = isPie
      ? chart.data.labels.map((l, i) => ({ i, label: l, color: chart.data.datasets[0].backgroundColor[i], value: chart.data.datasets[0].data[i] }))
      : chart.data.datasets.map((d, i) => ({ i, label: d.label, color: Array.isArray(d.backgroundColor) ? d.backgroundColor[0] : (d.borderColor || d.backgroundColor), value: values && values[i] }));
    container.classList.toggle('legend-list', !!list);
    container.innerHTML = items.map((it) => `<button type="button" class="legend-item" data-i="${it.i}"><span class="legend-name"><i class="dot" style="--c:${it.color}"></i><span class="truncate">${esc(it.label)}</span></span>${it.value != null ? `<span class="legend-val">${fmtMoney(it.value, currency)}</span>` : ''}</button>`).join('');
    container.onclick = (e) => {
      const b = e.target.closest('[data-i]'); if (!b) return;
      const i = Number(b.dataset.i);
      if (onClick) { onClick(items[i], e); return; }
      if (isPie) chart.toggleDataVisibility(i); else chart.setDatasetVisibility(i, !chart.isDatasetVisible(i));
      b.classList.toggle('is-off', isPie ? !chart.getDataVisibility(i) : !chart.isDatasetVisible(i));
      chart.update();
    };
  }

  /* Plugin: draws text in the center of a doughnut. Pass as plugins:[donutCenterPlugin(...)]. */
  function donutCenterPlugin(text, subtext) {
    return {
      id: 'donutCenter',
      afterDraw(chart) {
        const { ctx, chartArea } = chart; if (!chartArea) return;
        const t = theme();
        const cx = (chartArea.left + chartArea.right) / 2, cy = (chartArea.top + chartArea.bottom) / 2;
        ctx.save(); ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        const txt = typeof text === 'function' ? text() : text;
        const sub = typeof subtext === 'function' ? subtext() : subtext;
        ctx.fillStyle = t.text1; ctx.font = "600 20px 'Inter', system-ui, sans-serif"; ctx.fillText(txt, cx, cy - (sub ? 8 : 0));
        if (sub) { ctx.fillStyle = t.text; ctx.font = "500 11px 'Inter', system-ui, sans-serif"; ctx.fillText(sub, cx, cy + 12); }
        ctx.restore();
      },
    };
  }

  /* Common option builders */
  function barOptions(t, { currency = 'USD', stacked = false, horizontal = false } = {}) {
    return {
      indexAxis: horizontal ? 'y' : 'x',
      interaction: { mode: 'index', intersect: false },
      plugins: { tooltip: { callbacks: currencyTooltip(currency) } },
      scales: {
        x: { stacked, grid: { display: horizontal }, border: { display: !horizontal }, ticks: horizontal ? currencyTicks(currency) : { maxRotation: 0, autoSkip: true } },
        y: { stacked, beginAtZero: true, grid: { display: !horizontal }, border: { display: false }, ticks: horizontal ? {} : currencyTicks(currency) },
      },
    };
  }
  function lineOptions(t, { currency = 'USD' } = {}) {
    return {
      interaction: { mode: 'index', intersect: false },
      plugins: { tooltip: { callbacks: currencyTooltip(currency) } },
      scales: {
        x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true } },
        y: { beginAtZero: true, border: { display: false }, ticks: currencyTicks(currency) },
      },
    };
  }
  function sparkline(canvas, values, hex) {
    return makeChart(canvas, () => ({
      type: 'line',
      data: { labels: values.map((_, i) => i), datasets: [{ data: values, borderColor: hex, borderWidth: 1.5, fill: true, backgroundColor: (c) => gradientFill(c.chart.ctx, hex, { from: 0.25 }), pointRadius: 0, tension: 0.35 }] },
      options: { responsive: false, animation: false, plugins: { tooltip: { enabled: false } }, scales: { x: { display: false }, y: { display: false } }, elements: { point: { hitRadius: 0 } } },
    }));
  }

  return { theme, applyChartDefaults, catColor, withAlpha, gradientFill, currencyTicks, currencyTooltip, makeChart, destroyChart, rerenderAll, htmlLegend, donutCenterPlugin, barOptions, lineOptions, sparkline };
})();
const { makeChart, destroyChart, catColor } = charts;
