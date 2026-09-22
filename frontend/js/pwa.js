/* PWA bootstrap: registers the service worker and keeps hold of Chrome's install prompt.
   Loaded in <head> right after theme.js so the beforeinstallprompt listener exists before the
   browser decides installability. No DOM work here — nav.js and settings.js paint the UI. */
(function () {
  var prompt = null;

  function standalone() {
    return (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) || window.navigator.standalone === true;
  }
  function ios() {
    var ua = navigator.userAgent || '';
    return /iPad|iPhone|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  }

  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    prompt = e;
    window.dispatchEvent(new CustomEvent('ispend:pwa-installable'));
  });
  window.addEventListener('appinstalled', function () {
    prompt = null;
    window.dispatchEvent(new CustomEvent('ispend:pwa-installed'));
  });

  window.PWA = {
    canInstall: function () { return !!prompt; },
    isStandalone: standalone,
    isIOS: ios,
    /* Resolves true when the user accepted. The prompt is single-use, so it is dropped either way. */
    install: function () {
      if (!prompt) return Promise.resolve(false);
      var p = prompt; prompt = null;
      return p.prompt().then(function () { return p.userChoice; })
        .then(function (r) { return !!r && r.outcome === 'accepted'; })
        .catch(function () { return false; });
    },
  };

  /* Playwright's service_workers="block" stubs register() to resolve undefined; a plain http://host
     (not localhost) has no navigator.serviceWorker at all. Both are fine: the app never depends on it. */
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/sw.js').catch(function () { /* unsupported or blocked */ });
    });
  }
})();
