document.getElementById('login-mark').innerHTML = icon('activity');
const themeBtn = document.getElementById('login-theme');
const paint = () => { themeBtn.innerHTML = `${icon(Theme.effective() === 'dark' ? 'sun' : 'moon', 'ico-sm')} ${Theme.effective() === 'dark' ? 'Light' : 'Dark'} mode`; };
paint();
themeBtn.addEventListener('click', () => Theme.toggle());
window.addEventListener('ispend:theme', paint);

/* Only same-origin paths may be followed after login ("//evil.com" and "/\\evil.com" both resolve off-site). */
function safeNext(next) {
  if (!next) return '/index.html';
  try {
    const u = new URL(next, location.origin);
    if (u.origin !== location.origin || u.pathname.endsWith('/login.html')) return '/index.html';
    return u.pathname + u.search + u.hash;
  } catch { return '/index.html'; }
}

document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('login-btn');
  const errEl = document.getElementById('login-error');
  errEl.classList.remove('show');
  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value;
  if (!username || !password) {
    errEl.innerHTML = `${icon('alert-triangle')}<div>Enter your username and password.</div>`;
    errEl.classList.add('show');
    return;
  }
  btn.classList.add('is-loading');
  try {
    const me = await api('/api/auth/login', { method: 'POST', body: { username, password } });
    clearUserState();
    const theme = me && me.preferences && me.preferences.theme;
    if (theme && !Theme.hasStored()) Theme.set(theme);
    location.href = safeNext(new URLSearchParams(location.search).get('next'));
  } catch (err) {
    errEl.innerHTML = `${icon('alert-triangle')}<div>${esc(err.message)}</div>`;
    errEl.classList.add('show');
    document.getElementById('password').focus();
    document.getElementById('password').select();
  } finally {
    btn.classList.remove('is-loading');
  }
});
