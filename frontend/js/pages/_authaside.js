/* The marketing half of the sign-in screen, shared by all five unauthenticated pages.

   Injected rather than copied into each HTML file: the five pages differ only in their form, and
   a panel duplicated five times would drift. Hidden below 960px, where the pages fall back to the
   centred card they have always been. */
function mountAuthAside() {
  const wrap = document.querySelector('.login-wrap');
  const card = wrap && wrap.querySelector('.login-card');
  if (!card || wrap.querySelector('.auth-aside')) return;

  const col = document.createElement('div');
  col.className = 'auth-form';
  card.replaceWith(col);
  col.appendChild(card);

  const aside = document.createElement('aside');
  aside.className = 'auth-aside';
  aside.innerHTML = `
    <a class="auth-brand" href="/">
      <span class="auth-brand-mark">${brandMark('navy')}</span><span>iSpend</span>
    </a>
    <div class="auth-aside-in">
      <h2>Know where the money actually went.</h2>
      <p>iSpend reads the statements you already download — spreadsheets, CSVs, PDFs, even
         scans — and turns them into a clear picture of where the money went.</p>
      <ul>
        <li>${icon('check')}No bank logins, ever</li>
        <li>${icon('check')}Twelve banks work with no setup</li>
        <li>${icon('check')}Learns how you sort things</li>
      </ul>
    </div>
    <p class="auth-aside-foot">14-day free trial · no card required · cancel any time</p>`;
  wrap.insertBefore(aside, col);
}

mountAuthAside();
