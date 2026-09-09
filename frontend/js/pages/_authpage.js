/* Shared chrome for the unauthenticated pages (signup, forgot, reset, verify). */
function initAuthPage() {
  document.getElementById('login-mark').innerHTML = icon('activity');
  const themeBtn = document.getElementById('login-theme');
  const paint = () => {
    const dark = Theme.effective() === 'dark';
    themeBtn.innerHTML = `${icon(dark ? 'sun' : 'moon', 'ico-sm')} ${dark ? 'Light' : 'Dark'} mode`;
  };
  paint();
  themeBtn.addEventListener('click', () => Theme.toggle());
  window.addEventListener('ispend:theme', paint);
}

function showError(message) {
  const el = document.getElementById('login-error');
  el.innerHTML = `${icon('alert-triangle')}<div>${esc(message)}</div>`;
  el.classList.add('show');
}

function clearError() {
  document.getElementById('login-error').classList.remove('show');
}

function showNotice(message) {
  const el = document.getElementById('login-notice');
  el.innerHTML = `${icon('check-circle')}<div>${esc(message)}</div>`;
  el.hidden = false;
}
