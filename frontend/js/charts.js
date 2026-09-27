/* Chart.js helpers: theme-aware defaults, a registry that re-renders on theme
   change, category color binding, HTML legends, donut center plugin. */
const charts = (() => {
  const registry = new Map(); // canvas -> {chart, build}
  let still = false; // set by quietly(): charts drawn from a cached copy appear at once, without the grow-in

  /* Token reads are memoised per theme: each getComputedStyle read right after DOM writes forces a
     style recalculation, and a dashboard builds six charts of ~25 reads each. data-theme (and the
     reduced-motion setting) is the whole key — tokens change with nothing else. */
  let memo = { key: null, vars: new Map(), theme: null };
  // Read together on first use: one style recalculation instead of one per token met mid-render.
  const TOKENS = ['--text-3', '--text-1', '--text-2', '--chart-grid', '--chart-axis', '--surface', '--surface-overlay',
    '--border-strong', '--accent', '--chart-muted', '--success', '--danger', ...Array.from({ length: 12 }, (_, i) => `--c${i + 1}`)];
  function memoFor() {
    const key = `${document.documentElement.getAttribute('data-theme')}|${window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches}`;
    if (memo.key !== key) {
      const cs = getComputedStyle(document.documentElement);
      memo = { key, vars: new Map(TOKENS.map((t) => [t, cs.getPropertyValue(t).trim()])), theme: null };
    }
    return memo;
  }
  function css(name) {
    const m = memoFor();
    if (!m.vars.has(name)) m.vars.set(name, getComputedStyle(document.documentElement).getPropertyValue(name).trim());
    return m.vars.get(name);
  }
  function theme() {
    const m = memoFor();
    if (!m.theme) m.theme = readTheme();
    return m.theme;
  }
  function readTheme() {
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
    d.animation.duration = t.reduced || still ? 0 : 400;
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
    // on touch, charts built with tapToDrill say that a second tap opens what the first one shows
    d.plugins.tooltip.callbacks.footer = function footer() {
      const click = this.chart && this.chart.options.onClick;
      return click && click.isDrill && coarse() ? 'Tap again to open' : '';
    };
    d.plugins.tooltip.footerColor = t.text; d.plugins.tooltip.footerFont = { weight: '500', size: 11 };
    // phones: fewer, larger-spaced axis labels
    if (window.matchMedia('(max-width: 768px)').matches) d.scale.ticks.maxTicksLimit = 6;
  }

  const coarse = () => window.matchMedia('(pointer: coarse)').matches;
  /* tapToDrill(fn): a chart onClick for charts whose click navigates. With a mouse it navigates at
     once; on touch the first tap only shows the tooltip and a second tap on the same bar/slice
     navigates, so the numbers can be read before leaving the page. */
  function tapToDrill(fn) {
    let last = null;
    const handler = (evt, els, chart) => {
      if (!coarse()) return fn(evt, els, chart);
      const key = els && els.length ? `${els[0].datasetIndex}:${els[0].index}` : null;
      if (key && key === last) { last = null; return fn(evt, els, chart); }
      last = key;
      return undefined;
    };
    handler.isDrill = true;
    return handler;
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
  /* Run fn with chart animation off: a screen restored from its cached copy (or refreshed over one)
     should look like it never left, not replay its entrance. */
  function quietly(fn) {
    const was = still; still = true;
    try { return fn(); } finally { still = was; }
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
  function htmlLegend(container, chart, { onClick, values, currency = 'USD', list = false, format = 'money' } = {}) {
    const isPie = chart.config.type === 'doughnut' || chart.config.type === 'pie';
    const items = isPie
      ? chart.data.labels.map((l, i) => ({ i, label: l, color: chart.data.datasets[0].backgroundColor[i], value: chart.data.datasets[0].data[i] }))
      : chart.data.datasets.map((d, i) => ({ i, label: d.label, color: Array.isArray(d.backgroundColor) ? d.backgroundColor[0] : (d.borderColor || d.backgroundColor), value: values && values[i] }));
    container.classList.toggle('legend-list', !!list);
    container.innerHTML = items.map((it) => `<button type="button" class="legend-item" data-i="${it.i}"><span class="legend-name"><i class="dot" style="--c:${it.color}"></i><span class="truncate">${esc(it.label)}</span></span>${it.value != null ? `<span class="legend-val">${format === 'count' ? fmtNumber(it.value) : fmtMoney(it.value, currency)}</span>` : ''}</button>`).join('');
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
  /* Counts, not money (people, sign-ups): the same look as barOptions/lineOptions without currency. */
  function countOptions(t, { stacked = false, pct = false } = {}) {
    const f = (v) => (pct ? fmtPct(v, { decimals: 0 }) : fmtNumber(v));
    return {
      interaction: { mode: 'index', intersect: false },
      plugins: { tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${c.parsed.y == null ? '—' : f(c.parsed.y)}` } } },
      scales: {
        x: { stacked, grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true } },
        y: { stacked, beginAtZero: true, border: { display: false }, ticks: { precision: 0, callback: f } },
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
  /* Inline SVG sparkline: Chart.js's tension-0.35 curve over a fading area, as markup. A Chart.js
     instance per 64×26 glyph cost ~10 ms each on a phone; this is a string. `color` is any CSS colour,
     normally var(--token), so the line follows the theme without a redraw. */
  let sparkSeq = 0;
  function sparkSvg(values, color, { width = 64, height = 26, fill = 0.22, stroke = 1.5, cls = '' } = {}) {
    const n = (values || []).length;
    const id = `spark-g${++sparkSeq}`;
    const open = `<svg class="spark ${cls}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true" focusable="false">`;
    if (!n) return `${open}</svg>`;
    const pad = stroke;
    const min = Math.min(...values), max = Math.max(...values), span = max - min;
    const pts = values.map((v, i) => [n === 1 ? width / 2 : (i / (n - 1)) * width, span ? pad + (1 - (v - min) / span) * (height - 2 * pad) : height / 2]);
    const f = (x) => Math.round(x * 100) / 100;
    // Chart.js splineCurve: each point's control points lie along the neighbours' chord, scaled by tension.
    const ctrl = pts.map((p, i) => {
      const a = pts[i - 1] || p, c = pts[i + 1] || p;
      const d01 = Math.hypot(p[0] - a[0], p[1] - a[1]), d12 = Math.hypot(c[0] - p[0], c[1] - p[1]);
      const fa = d01 + d12 ? 0.35 * d01 / (d01 + d12) : 0, fb = d01 + d12 ? 0.35 * d12 / (d01 + d12) : 0;
      const cap = (y) => Math.min(Math.max(y, pad), height - pad); // like Chart.js capBezierPoints: no overshoot past the box
      return { prev: [p[0] - fa * (c[0] - a[0]), cap(p[1] - fa * (c[1] - a[1]))], next: [p[0] + fb * (c[0] - a[0]), cap(p[1] + fb * (c[1] - a[1]))] };
    });
    let d = `M${f(pts[0][0])},${f(pts[0][1])}`;
    for (let i = 1; i < n; i++) d += `C${f(ctrl[i - 1].next[0])},${f(ctrl[i - 1].next[1])} ${f(ctrl[i].prev[0])},${f(ctrl[i].prev[1])} ${f(pts[i][0])},${f(pts[i][1])}`;
    return `${open}<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:${color};stop-opacity:${fill}"/><stop offset="1" style="stop-color:${color};stop-opacity:0"/></linearGradient></defs>`
      + `<path d="${d}L${width},${height}L0,${height}Z" fill="url(#${id})"/>`
      + `<path d="${d}" fill="none" style="stroke:${color}" stroke-width="${stroke}" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>`;
  }

  return { theme, css, sparkSvg, applyChartDefaults, tapToDrill, catColor, withAlpha, gradientFill, currencyTicks, currencyTooltip, makeChart, destroyChart, quietly, rerenderAll, htmlLegend, donutCenterPlugin, barOptions, lineOptions, countOptions };
})();
const { makeChart, destroyChart, catColor } = charts;
