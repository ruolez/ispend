initAuthPage();

const token = new URLSearchParams(location.search).get('token') || '';

if (!token) {
  $('#page-body').innerHTML = ui.emptyState({
    icon: 'alert-triangle', title: 'That link is incomplete',
    body: 'Open the link from your email again, or ask for a new one.',
    action: { label: 'Request a new link', href: '/forgot.html' } });
} else {
  $('#page-body').innerHTML = `
    <form id="reset-form" novalidate>
      <div class="field"><label for="password">New password</label>
        <input class="input" type="password" id="password" autocomplete="new-password" minlength="10" required autofocus>
        <div class="hint">At least 10 characters.</div></div>
      <div class="field"><label for="confirm">Confirm</label>
        <input class="input" type="password" id="confirm" autocomplete="new-password" required></div>
      <button type="submit" class="btn btn-primary btn-block" id="reset-btn">Set password and sign in</button>
    </form>`;

  $('#reset-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    clearError();
    const password = $('#password').value;
    if (password !== $('#confirm').value) return showError('Those passwords do not match.');
    try {
      await ui.busy($('#reset-btn'), async () => {
        await api('/api/auth/password/reset', { method: 'POST', body: { token, password } });
        clearUserState();
        location.href = '/index.html';
      }, { silent: true, rethrow: true });
    } catch (err) {
      showError(err.message);
    }
    return undefined;
  });
}
