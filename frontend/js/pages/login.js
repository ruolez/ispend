document.getElementById('login-mark').innerHTML = brandMark();
const pwToggle = document.getElementById('login-pw-toggle');
pwToggle.innerHTML = icon('eye');
pwToggle.addEventListener('click', () => {
  const pw = document.getElementById('password');
  pw.type = pw.type === 'password' ? 'text' : 'password';
  pwToggle.innerHTML = icon(pw.type === 'password' ? 'eye' : 'eye-off');
  pwToggle.setAttribute('aria-label', pw.type === 'password' ? 'Show password' : 'Hide password');
  pw.focus();
});
const themeBtn = document.getElementById('login-theme');
const paint = () => { themeBtn.innerHTML = `${icon(Theme.effective() === 'dark' ? 'sun' : 'moon', 'ico-sm')} ${Theme.effective() === 'dark' ? 'Light' : 'Dark'} mode`; };
paint();
themeBtn.addEventListener('click', () => Theme.toggle());
window.addEventListener('ispend:theme', paint);

/* Only offered when the server accepts new accounts. */
api('/api/auth/public-config')
  .then((cfg) => { if (cfg.signup_enabled) document.getElementById('signup-link').hidden = false; })
  .catch(() => { /* leave it hidden */ });

/* Sent back here by api.js after a 401 in a browser that had signed in before, or by verify.html. */
const noticeEl = document.getElementById('login-notice');
function notice(html) {
  noticeEl.innerHTML = html;
  noticeEl.hidden = false;
}
const params = new URLSearchParams(location.search);
if (params.get('reason') === 'expired') {
  notice(`${icon('info')}<div>Your session expired. Sign in again to continue.</div>`);
} else if (params.get('verified') === '1') {
  notice(`${icon('check-circle')}<div>Email confirmed. Sign in to get started.</div>`);
}

/* A self-signup that has not opened its link yet: the address is the username, so a resend can
   be offered right here. */
function noticeUnverified(message, email) {
  notice(`${icon('mail')}<div>${esc(message)}
    <div class="mt-2"><button type="button" class="btn btn-secondary btn-sm" id="login-resend">Resend the email</button></div></div>`);
  const btn = document.getElementById('login-resend');
  btn.addEventListener('click', async () => {
    const sent = await ui.busy(btn, async () => {
      await api('/api/auth/email/resend-pending', { method: 'POST', body: { email } });
      return true;
    });
    if (sent) { btn.textContent = 'Sent'; btn.disabled = true; }
  });
}
const capsHint = document.getElementById('caps-hint');
const pwField = document.getElementById('password');
const paintCaps = (e) => { capsHint.hidden = !(e.getModifierState && e.getModifierState('CapsLock')); };
pwField.addEventListener('keydown', paintCaps);
pwField.addEventListener('keyup', paintCaps);
pwField.addEventListener('blur', () => { capsHint.hidden = true; });

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
  try {
    await ui.busy(btn, async () => {
      const me = await api('/api/auth/login', { method: 'POST', body: { username, password } });
      clearUserState();
      const theme = me && me.preferences && me.preferences.theme;
      if (theme && !Theme.hasStored()) Theme.set(theme);
      location.href = safeNext(new URLSearchParams(location.search).get('next'));
    }, { silent: true, rethrow: true });
  } catch (err) {
    if (err.status === 403 && err.data && err.data.code === 'email_unverified') {
      return noticeUnverified(err.message, username);
    }
    errEl.innerHTML = `${icon('alert-triangle')}<div>${esc(err.message)}</div>`;
    errEl.classList.add('show');
    document.getElementById('password').focus();
    document.getElementById('password').select();
  }
});
