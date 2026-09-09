initAuthPage();

const token = new URLSearchParams(location.search).get('token') || '';

(async () => {
  if (!token) {
    $('#page-body').innerHTML = ui.emptyState({ icon: 'alert-triangle', title: 'That link is incomplete',
      body: 'Open the link from your email again.' });
    return;
  }
  $('#page-body').innerHTML = ui.skeletonList(1);
  try {
    await api('/api/auth/email/verify', { method: 'POST', body: { token } });
  } catch (err) {
    $('#page-body').innerHTML = '';
    showError(err.message);
    return;
  }
  $('#page-body').innerHTML = ui.emptyState({ icon: 'check-circle', title: 'Email confirmed',
    body: 'You can subscribe whenever you are ready.',
    action: { label: 'Continue to iSpend', href: '/index.html' } });
})();
