initAuthPage();

const token = new URLSearchParams(location.search).get('token') || '';

(async () => {
  if (!token) {
    $('#page-body').innerHTML = ui.emptyState({ icon: 'alert-triangle', title: 'That link is incomplete',
      body: 'Open the link from your email again.' });
    return;
  }
  $('#page-body').innerHTML = ui.skeletonList(1);
  let r;
  try {
    r = await api('/api/auth/email/verify', { method: 'POST', body: { token } });
  } catch (err) {
    $('#page-body').innerHTML = '';
    showError(err.message);
    return;
  }
  // A pending sign-up lands here signed out; the link confirms the address, it does not sign in.
  $('#page-body').innerHTML = ui.emptyState({ icon: 'check-circle', title: 'Email confirmed',
    ...(r.signed_in
      ? { body: 'You can subscribe whenever you are ready.', action: { label: 'Continue to iSpend', href: '/index.html' } }
      : { body: 'Sign in to get started.', action: { label: 'Sign in', href: '/login.html?verified=1' } }) });
})();
