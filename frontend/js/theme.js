/* Theme + density bootstrap. Loaded synchronously in <head> BEFORE the
   stylesheets so the first paint is already the right theme. */
(function () {
  var THEME_KEY = 'ispend.theme';
  var DENSITY_KEY = 'ispend.density';
  var root = document.documentElement;
  var mql = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function read(key, fallback) {
    try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
  }

  function apply(mode) {
    if (mode === 'light' || mode === 'dark') root.setAttribute('data-theme', mode);
    else root.removeAttribute('data-theme');
    root.setAttribute('data-theme-mode', mode);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', effective() === 'dark' ? '#0c0e13' : '#f5f6f8');
  }

  function effective() {
    var mode = read(THEME_KEY, 'system');
    if (mode === 'system') return mql && mql.matches ? 'dark' : 'light';
    return mode;
  }

  function dispatch() {
    window.dispatchEvent(new CustomEvent('ispend:theme', { detail: { mode: read(THEME_KEY, 'system'), effective: effective() } }));
  }

  function set(mode) {
    if (mode !== 'light' && mode !== 'dark') mode = 'system';
    write(THEME_KEY, mode);
    root.classList.add('theme-switching');
    apply(mode);
    requestAnimationFrame(function () { requestAnimationFrame(function () { root.classList.remove('theme-switching'); }); });
    dispatch();
  }

  function applyDensity(d) {
    if (d === 'compact') root.setAttribute('data-density', 'compact');
    else root.removeAttribute('data-density');
  }

  window.Theme = {
    get: function () { return read(THEME_KEY, 'system'); },
    effective: effective,
    set: set,
    toggle: function () { set(effective() === 'dark' ? 'light' : 'dark'); },
    hasStored: function () { try { return !!localStorage.getItem(THEME_KEY); } catch (e) { return false; } },
    density: function () { return read(DENSITY_KEY, 'comfortable'); },
    setDensity: function (d) { write(DENSITY_KEY, d); applyDensity(d); window.dispatchEvent(new CustomEvent('ispend:density', { detail: d })); },
    hasStoredDensity: function () { try { return !!localStorage.getItem(DENSITY_KEY); } catch (e) { return false; } },
  };

  apply(read(THEME_KEY, 'system'));
  applyDensity(read(DENSITY_KEY, 'comfortable'));
  if (mql && mql.addEventListener) {
    mql.addEventListener('change', function () { if (read(THEME_KEY, 'system') === 'system') { apply('system'); dispatch(); } });
  }
})();
