/* The problem-report form (nav.js openReport loads this on first use; help.html ships it).

   A short form in the words of the problem, not the software: what kind of thing it is, what
   happened, optionally how badly it gets in the way, and screenshots the customer adds themselves.
   Technical details are allow-listed (api.js diag + a few facts about the browser), shown in full
   before sending, and can be left out. Nothing on the screen is captured automatically. */
const ReportForm = (() => {
  const KINDS = [
    { id: 'bug', label: 'Something isn’t working', icon: 'alert-triangle',
      body: 'What happened?', hint: 'What were you doing, what went wrong, and what did you expect instead?' },
    { id: 'data', label: 'My numbers look wrong', icon: 'activity',
      body: 'What looks wrong?', hint: 'Which page, account or month, and what did you expect to see?' },
    { id: 'question', label: 'Question', icon: 'help',
      body: 'Your question', hint: 'Ask anything about using iSpend.' },
    { id: 'idea', label: 'Idea or request', icon: 'lightbulb',
      body: 'Your idea', hint: 'What would you like iSpend to do, and what would it help you with?' },
    { id: 'billing', label: 'Billing and account', icon: 'credit-card',
      body: 'What do you need help with?', hint: 'Tell us what happened with your plan, payment or sign-in.' },
  ];
  const IMPACTS = [['blocking', 'Stops me using iSpend'], ['annoying', 'Annoying, but I can work around it'], ['minor', 'Minor']];
  const TECHNICAL = new Set(['bug', 'data']);
  const MAX_FILES = 3;
  const MAX_BYTES = 5 * 1024 * 1024;
  const TYPES = ['image/png', 'image/jpeg', 'image/webp'];
  const BODY_MIN = 10;
  const DRAFT_KEY = 'ispend.reportDraft';

  function readDraft() { try { return JSON.parse(sessionStorage.getItem(DRAFT_KEY)) || null; } catch { return null; } }
  function saveDraft(d) { try { sessionStorage.setItem(DRAFT_KEY, JSON.stringify(d)); } catch { /* ignore */ } }
  function clearDraft() { try { sessionStorage.removeItem(DRAFT_KEY); } catch { /* ignore */ } }

  /* The page the problem is about: the form's own page, or the one Help was opened from. */
  function reportedPage() {
    if (!location.pathname.endsWith('/help.html')) return location.pathname;
    try {
      const ref = new URL(document.referrer);
      if (ref.origin === location.origin && !ref.pathname.endsWith('/help.html')) return ref.pathname;
    } catch { /* no referrer */ }
    return location.pathname;
  }

  async function buildId() {
    try {
      const names = window.caches ? await caches.keys() : [];
      const shell = names.find((n) => n.startsWith('ispend-shell-'));
      return shell ? shell.slice('ispend-shell-'.length) : 'dev';
    } catch { return undefined; }
  }

  async function technicalDetails(error) {
    const d = diag.snapshot();
    const out = {
      page: reportedPage(),
      build: await buildId(),
      viewport: `${window.innerWidth}x${window.innerHeight}@${Math.round((window.devicePixelRatio || 1) * 100) / 100}`,
      theme: window.Theme ? Theme.effective() : undefined,
      standalone: !!(window.PWA && PWA.isStandalone()),
      online: navigator.onLine !== false,
      language: navigator.language,
      timezone: (Intl.DateTimeFormat().resolvedOptions() || {}).timeZone,
      request_id: (error && error.requestId) || undefined,
      errors: d.errors,
      requests: d.requests,
    };
    Object.keys(out).forEach((k) => { if (out[k] === undefined || (Array.isArray(out[k]) && !out[k].length)) delete out[k]; });
    return out;
  }

  function faqMatches(text, limit = 3) {
    if (typeof HELP_FAQ === 'undefined') return [];
    const words = String(text || '').toLowerCase().match(/[a-z]{3,}/g) || [];
    if (words.length < 2) return [];
    const stop = new Set(['the', 'and', 'for', 'with', 'this', 'that', 'what', 'when', 'from', 'have', 'does', 'not', 'but', 'was', 'are', 'can', 'how', 'why', 'any', 'you', 'your', 'isn', 'doesn', 'there', 'they', 'into', 'just', 'like', 'get', 'got']);
    const terms = words.filter((w) => !stop.has(w));
    return HELP_FAQ.map((f) => {
      const hay = `${f.q} ${f.k}`.toLowerCase();
      return { f, score: terms.reduce((n, w) => n + (hay.includes(w) ? 1 : 0), 0) };
    }).filter((x) => x.score >= 2).sort((a, b) => b.score - a.score).slice(0, limit).map((x) => x.f);
  }

  const STATUS = {
    open: ['Open', 'badge-info'], in_progress: ['In progress', 'badge-accent'],
    waiting: ['Waiting for you', 'badge-warning'], resolved: ['Resolved', 'badge-success'],
  };
  function statusBadge(status) {
    const [label, cls] = STATUS[status] || [status, 'badge-neutral'];
    return `<span class="badge ${cls}">${esc(label)}</span>`;
  }

  /* Screenshot picker: markup plus behaviour, shared by the form and the reply box on Help. */
  function shotsHtml(labelId) {
    return `<div class="rp-files"></div>
      <label class="rp-drop" tabindex="0" ${labelId ? `aria-labelledby="${labelId}"` : 'aria-label="Add screenshots"'}>
        <input type="file" class="rp-file" accept="${TYPES.join(',')}" multiple>
        ${icon('upload', 'ico-sm')}<span class="rp-drop-mouse">Drop, paste or <span class="text-accent">choose images</span></span><span class="rp-drop-touch">Add a screenshot</span>
      </label>`;
  }
  function mountShots(scope, pasteRoot = scope) {
    const shots = [];
    const drop = $('.rp-drop', scope);
    const paint = () => {
      $('.rp-files', scope).innerHTML = shots.map((f, i) => `<div class="rp-thumb">
        ${f.url ? `<img src="${esc(f.url)}" alt="Screenshot ${i + 1}: ${esc(f.file.name || 'pasted image')}">` : ''}
        <button type="button" class="btn btn-icon btn-sm rp-thumb-x" data-remove-shot="${i}" aria-label="Remove screenshot ${i + 1}">${icon('x', 'ico-sm')}</button></div>`).join('');
      drop.hidden = shots.length >= MAX_FILES;
    };
    const add = (list) => {
      for (const file of Array.from(list || [])) {
        if (shots.length >= MAX_FILES) { toast(`You can add up to ${MAX_FILES} screenshots`, { type: 'error' }); break; }
        if (!TYPES.includes(file.type)) { toast(`${file.name || 'That file'} isn’t a PNG, JPEG or WebP image`, { type: 'error' }); continue; }
        if (file.size > MAX_BYTES) { toast(`${file.name || 'That image'} is over 5 MB`, { type: 'error' }); continue; }
        const entry = { file, url: '' };
        shots.push(entry);
        // A data: URL, not blob: — the app's content security policy allows only data: images.
        const reader = new FileReader();
        reader.onload = () => { entry.url = reader.result; paint(); };
        reader.readAsDataURL(file);
      }
      paint();
    };
    scope.addEventListener('change', (e) => { if (e.target.classList.contains('rp-file')) { add(e.target.files); e.target.value = ''; } });
    scope.addEventListener('click', (e) => {
      const rm = e.target.closest('[data-remove-shot]');
      if (rm) { shots.splice(Number(rm.dataset.removeShot), 1); paint(); }
    });
    pasteRoot.addEventListener('paste', (e) => {
      const files = Array.from((e.clipboardData && e.clipboardData.files) || []).filter((f) => f.type.startsWith('image/'));
      if (files.length) { e.preventDefault(); add(files); }
    });
    ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('is-dragover'); }));
    ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('is-dragover'); }));
    drop.addEventListener('drop', (e) => add(e.dataTransfer.files));
    drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('.rp-file', scope).click(); } });
    paint();
    return { files: () => shots.map((f) => f.file), count: () => shots.length, clear: () => { shots.length = 0; paint(); } };
  }

  function formHtml(state) {
    return `<form class="rp" novalidate>
      <fieldset class="rp-kinds"><legend class="label">What is this about?</legend>
        ${KINDS.map((k) => `<label class="rp-kind"><input type="radio" name="rp-kind" value="${k.id}" ${state.kind === k.id ? 'checked' : ''}>${icon(k.icon)}<span>${esc(k.label)}</span></label>`).join('')}
      </fieldset>
      <div class="field"><label for="rp-body" data-required id="rp-body-label"></label>
        <textarea id="rp-body" class="textarea" rows="5" maxlength="8000" required></textarea>
        <div class="hint">Please don’t include passwords or full account and card numbers.</div></div>
      <div class="rp-suggest" id="rp-suggest" hidden></div>
      <div class="field"><label for="rp-subject">Summary <span class="text-3 fw-500">(optional)</span></label>
        <input id="rp-subject" class="input" maxlength="140" autocomplete="off">
        <div class="hint">If you leave this empty, we use the first line.</div></div>
      <fieldset class="field rp-impact" id="rp-impact"><legend class="label">How much does it get in the way? <span class="text-3 fw-500">(optional)</span></legend>
        <div class="rp-impacts">${IMPACTS.map(([v, l]) => `<label class="rp-chip"><input type="radio" name="rp-impact" value="${v}" ${state.impact === v ? 'checked' : ''}><span>${esc(l)}</span></label>`).join('')}</div>
      </fieldset>
      <div class="field rp-shots"><span class="label" id="rp-files-label">Screenshots <span class="text-3 fw-500">(optional, up to ${MAX_FILES})</span></span>
        ${shotsHtml('rp-files-label')}
        <div class="hint">PNG, JPEG or WebP, up to 5 MB each. Crop out anything private first. Location data in photos is removed.</div></div>
      <div class="rp-tech">
        <label class="rp-tech-toggle"><input type="checkbox" class="check" id="rp-tech" ${state.technical ? 'checked' : ''}><span>Include technical details</span></label>
        <details class="rp-tech-details"><summary>See exactly what’s sent</summary>
          <p class="hint">The page you were on, your browser and screen size, and the last few errors in this tab, with amounts, numbers and email addresses masked. No transactions, balances or files.</p>
          <pre class="rp-json" id="rp-json" tabindex="0"></pre></details>
      </div>
    </form>`;
  }

  function open(prefill = {}) {
    const draft = readDraft() || {};
    const state = {
      kind: prefill.kind || draft.kind || 'bug',
      impact: draft.impact || null,
      technical: null,
      techTouched: false,
      error: prefill.error || null,
      tech: {},
    };
    state.technical = TECHNICAL.has(state.kind);
    let sent = false;
    const m = ui.modal({
      title: 'Report a problem',
      size: 'lg',
      className: 'rp-modal',
      html: formHtml(state),
      actions: [
        { label: 'Cancel' },
        { label: 'Send report', primary: true, onClick: () => submit() },
      ],
      onClose: () => { if (!sent) persist(); },
    });
    const el = m.el;
    const body = $('#rp-body', el);
    const subject = $('#rp-subject', el);
    body.value = prefill.body || draft.body || '';
    subject.value = prefill.subject || draft.subject || '';

    function persist() {
      if (body.value.trim() || subject.value.trim()) saveDraft({ kind: state.kind, body: body.value, subject: subject.value, impact: state.impact });
      else clearDraft();
    }

    function paintKind() {
      const k = KINDS.find((x) => x.id === state.kind) || KINDS[0];
      $('#rp-body-label', el).textContent = k.body;
      body.placeholder = k.hint;
      $('#rp-impact', el).hidden = !TECHNICAL.has(state.kind);
      if (!state.techTouched) { state.technical = TECHNICAL.has(state.kind); $('#rp-tech', el).checked = state.technical; }
      m.setTitle(state.kind === 'question' ? 'Ask a question' : state.kind === 'idea' ? 'Share an idea' : 'Report a problem');
    }

    async function paintTech() {
      state.tech = await technicalDetails(state.error);
      $('#rp-json', el).textContent = JSON.stringify(state.tech, null, 2);
      $('.rp-tech-details', el).classList.toggle('is-off', !state.technical);
    }

    const paintSuggest = debounce(() => {
      const host = $('#rp-suggest', el);
      const found = faqMatches(`${subject.value} ${body.value}`);
      host.hidden = !found.length;
      host.innerHTML = found.length ? `<div class="section-label">These answers might help</div>${found.map((f) => `<details class="rp-answer"><summary>${esc(f.q)}</summary><p>${f.a}</p></details>`).join('')}` : '';
    }, 250);

    const shots = mountShots($('.rp-shots', el), el);

    el.addEventListener('change', (e) => {
      if (e.target.name === 'rp-kind') { state.kind = e.target.value; paintKind(); paintSuggest(); }
      if (e.target.name === 'rp-impact') state.impact = e.target.value;
      if (e.target.id === 'rp-tech') { state.technical = e.target.checked; state.techTouched = true; paintTech(); }
    });
    body.addEventListener('input', () => { ui.fieldError(body, null); paintSuggest(); });
    subject.addEventListener('input', paintSuggest);

    async function submit() {
      const text = body.value.trim();
      if (!text) { ui.fieldError(body, 'Tell us what happened'); body.focus(); return false; }
      if (text.length < BODY_MIN) { ui.fieldError(body, `Add a little more detail (at least ${BODY_MIN} characters)`); body.focus(); return false; }
      const fd = new FormData();
      fd.append('kind', state.kind);
      fd.append('body', text);
      if (subject.value.trim()) fd.append('subject', subject.value.trim());
      if (state.impact && TECHNICAL.has(state.kind)) fd.append('impact', state.impact);
      if (state.technical) fd.append('context', JSON.stringify(await technicalDetails(state.error)));
      shots.files().forEach((f) => fd.append('files', f, f.name || 'screenshot.png'));
      const report = await apiUpload('/api/support/reports', fd);
      store.afterWrite('/api/support/reports');
      sent = true;
      clearDraft();
      window.dispatchEvent(new CustomEvent('ispend:report-sent', { detail: report }));
      showSent(report);
      return false;
    }

    function showSent(report) {
      const me = window.currentUser || {};
      m.setTitle('Report sent');
      m.body.innerHTML = `<div class="rp-sent" role="status">
        <div class="rp-sent-icon">${icon('check-circle')}</div>
        <div class="rp-sent-title">Thanks — we have your report</div>
        <div class="rp-ref">Reference <b class="mono">${esc(report.ref)}</b></div>
        <p class="text-2">We’ll answer in iSpend${me.email ? ' and email you at ' + esc(me.email) : ''}. You can add details or screenshots to it at any time from Help.</p>
      </div>`;
      const foot = el.querySelector('.modal-foot');
      foot.innerHTML = `<button type="button" class="btn btn-secondary" data-act="rp-done">Done</button><a class="btn btn-primary" href="/help.html?report=${encodeURIComponent(report.id)}">View report</a>`;
      foot.querySelector('[data-act="rp-done"]').addEventListener('click', () => m.close());
      if (location.pathname.endsWith('/help.html')) {
        foot.querySelector('a').addEventListener('click', (e) => { e.preventDefault(); m.close(); window.dispatchEvent(new CustomEvent('ispend:open-report', { detail: report })); });
      }
      foot.querySelector('.btn-primary').focus();
    }

    paintKind();
    paintTech();
    paintSuggest();
    return m;
  }

  return { open, faqMatches, KINDS, STATUS, statusBadge, shotsHtml, mountShots, MAX_FILES };
})();
