/* Intl-based formatters. Locale follows the browser. */
const LOCALE = (typeof navigator !== 'undefined' && navigator.language) || 'en-US';
const _nf = new Map();
function _numFmt(key, opts) {
  if (!_nf.has(key)) _nf.set(key, new Intl.NumberFormat(LOCALE, opts));
  return _nf.get(key);
}
const MINUS = '−';

/* fmtMoney(-6.45, 'USD') -> "−$6.45"; {compact:true} -> "$1.2K"; {sign:'always'} -> "+$6.45".
   {abs:true} drops the sign. Uses a real minus sign. */
function fmtMoney(n, currency = 'USD', { compact = false, sign = 'auto', abs = false, decimals } = {}) {
  if (n == null || n === '' || Number.isNaN(Number(n))) return '—';
  let v = Number(n);
  if (abs) v = Math.abs(v);
  const neg = v < 0;
  const a = Math.abs(v);
  const cur = currency || 'USD';
  let opts;
  if (compact) opts = { style: 'currency', currency: cur, currencyDisplay: 'narrowSymbol', notation: 'compact', maximumFractionDigits: a < 1000 ? 0 : 1 };
  else opts = { style: 'currency', currency: cur, currencyDisplay: 'narrowSymbol', minimumFractionDigits: decimals ?? 2, maximumFractionDigits: decimals ?? 2 };
  const key = `${cur}|${compact}|${decimals}`;
  let s;
  try { s = _numFmt(key, opts).format(a); } catch { s = a.toFixed(2); }
  if (neg) return MINUS + s;
  if (sign === 'always' && v > 0) return '+' + s;
  return s;
}

function fmtNumber(n, { decimals = 0, compact = false } = {}) {
  if (n == null || Number.isNaN(Number(n))) return '—';
  const opts = compact ? { notation: 'compact', maximumFractionDigits: 1 } : { minimumFractionDigits: decimals, maximumFractionDigits: decimals };
  return _numFmt(`n|${decimals}|${compact}`, opts).format(Number(n));
}

function fmtPct(x, { decimals = 1, sign = false } = {}) {
  if (x == null || Number.isNaN(Number(x))) return '—';
  const v = Number(x);
  const s = _numFmt(`p|${decimals}`, { style: 'percent', minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(Math.abs(v));
  if (v < 0) return MINUS + s;
  return (sign && v > 0 ? '+' : '') + s;
}

function _toDate(v) {
  if (v == null || v === '') return null;
  if (v instanceof Date) return v;
  const s = String(v);
  // Date-only ISO strings must not shift by timezone.
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

/* fmtDate('2026-09-02') -> "Sep 2" (adds year when not the current year or {year:true}). */
function fmtDate(v, { year } = {}) {
  const d = _toDate(v);
  if (!d) return '—';
  const showYear = year === true || (year !== false && d.getFullYear() !== new Date().getFullYear());
  return d.toLocaleDateString(LOCALE, { month: 'short', day: 'numeric', ...(showYear ? { year: 'numeric' } : {}) });
}
function fmtDateLong(v) {
  const d = _toDate(v);
  return d ? d.toLocaleDateString(LOCALE, { weekday: 'short', month: 'long', day: 'numeric', year: 'numeric' }) : '—';
}
function fmtDateTime(v) {
  const d = _toDate(v);
  return d ? d.toLocaleString(LOCALE, { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';
}
/* fmtMonth('2026-09') -> "Sep ’26"; {long:true} -> "September 2026". */
function fmtMonth(ym, { long = false } = {}) {
  if (!ym) return '—';
  const [y, m] = String(ym).slice(0, 7).split('-').map(Number);
  const d = new Date(y, (m || 1) - 1, 1);
  if (long) return d.toLocaleDateString(LOCALE, { month: 'long', year: 'numeric' });
  return d.toLocaleDateString(LOCALE, { month: 'short' }) + ' ’' + String(y).slice(2);
}
function fmtRelative(v) {
  const d = _toDate(v);
  if (!d) return '—';
  const diff = (d.getTime() - Date.now()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(LOCALE, { numeric: 'auto' });
  const abs = Math.abs(diff);
  if (abs < 60) return rtf.format(Math.round(diff), 'second');
  if (abs < 3600) return rtf.format(Math.round(diff / 60), 'minute');
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), 'hour');
  if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), 'day');
  if (abs < 86400 * 365) return rtf.format(Math.round(diff / (86400 * 30)), 'month');
  return rtf.format(Math.round(diff / (86400 * 365)), 'year');
}
function toISODate(d) {
  const x = _toDate(d) || new Date();
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`;
}

/* fmtDelta(120, 100) -> {pct: 0.2, dir: 'up', text: '20.0%'}; prev 0 -> {dir:'flat', text:'—'} */
function fmtDelta(cur, prev) {
  const c = Number(cur) || 0;
  const p = Number(prev) || 0;
  if (!p) return { pct: null, dir: 'flat', text: '—' };
  const pct = (c - p) / Math.abs(p);
  const dir = Math.abs(pct) < 0.0005 ? 'flat' : (pct > 0 ? 'up' : 'down');
  return { pct, dir, text: fmtPct(Math.abs(pct)) };
}

function fmtBytes(n) {
  if (n == null) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let v = Number(n), i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

function initials(name) {
  const words = String(name || '').trim().split(/[\s._-]+/).filter(Boolean);
  if (!words.length) return '?';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

function plural(n, one, many) { return `${fmtNumber(n)} ${Number(n) === 1 ? one : (many || one + 's')}`; }
