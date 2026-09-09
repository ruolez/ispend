initAuthPage();

$('#page-body').innerHTML = `
  <form id="signup-form" novalidate>
    <div class="field"><label for="email">Email</label>
      <input class="input" type="email" id="email" autocomplete="email" required autofocus spellcheck="false"></div>
    <div class="field"><label for="password">Password</label>
      <div class="input-group"><input class="input has-trailing" type="password" id="password"
        autocomplete="new-password" minlength="10" required aria-describedby="pw-hint">
        <button type="button" class="btn btn-icon btn-ghost btn-sm trailing" id="pw-toggle" aria-label="Show password"></button></div>
      <div class="hint" id="pw-hint">At least 10 characters.</div></div>
    <button type="submit" class="btn btn-primary btn-block" id="signup-btn">Create account</button>
  </form>`;

const toggle = $('#pw-toggle');
toggle.innerHTML = icon('eye');
toggle.addEventListener('click', () => {
  const pw = $('#password');
  pw.type = pw.type === 'password' ? 'text' : 'password';
  toggle.innerHTML = icon(pw.type === 'password' ? 'eye' : 'eye-off');
  toggle.setAttribute('aria-label', pw.type === 'password' ? 'Show password' : 'Hide password');
  pw.focus();
});

api('/api/auth/public-config').then((cfg) => {
  if (!cfg.signup_enabled) {
    $('#page-body').innerHTML = ui.emptyState({
      icon: 'lock', title: 'Sign-ups are closed',
      body: 'This iSpend server does not accept new accounts. Ask an administrator for one.',
      action: { label: 'Back to sign in', href: '/login.html' } });
  }
}).catch(() => { /* the form still posts; the server decides */ });

$('#signup-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  clearError();
  const email = $('#email').value.trim();
  const password = $('#password').value;
  if (!email || !password) return showError('Enter an email address and a password.');
  try {
    await ui.busy($('#signup-btn'), async () => {
      await api('/api/auth/signup', { method: 'POST', body: { email, password } });
      clearUserState();
      location.href = '/index.html';
    }, { silent: true, rethrow: true });
  } catch (err) {
    if (err.data && err.data.code === 'email_taken') {
      return showError('That email already has an account. Sign in instead.');
    }
    showError(err.message);
  }
  return undefined;
});
