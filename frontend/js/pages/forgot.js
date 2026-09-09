initAuthPage();

$('#page-body').innerHTML = `
  <form id="forgot-form" novalidate>
    <div class="field"><label for="email">Email</label>
      <input class="input" type="email" id="email" autocomplete="email" required autofocus spellcheck="false"></div>
    <button type="submit" class="btn btn-primary btn-block" id="forgot-btn">Email me a link</button>
  </form>`;

$('#forgot-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  clearError();
  const email = $('#email').value.trim();
  if (!email) return showError('Enter your email address.');
  await ui.busy($('#forgot-btn'), async () => {
    await api('/api/auth/password/forgot', { method: 'POST', body: { email } });
    // Always the same answer, whether or not the address exists.
    $('#page-body').innerHTML = '';
    showNotice('If that address has an account, we have sent a link. It is valid for one hour.');
  });
  return undefined;
});
