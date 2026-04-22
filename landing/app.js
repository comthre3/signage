/* ─────────────────────────────────────────────────────────────
   Sawwii landing — client behaviour
   ───────────────────────────────────────────────────────────── */
(function () {
  'use strict';

  const APP_URL = (window.APP_URL || 'https://app.sawwii.com').replace(/\/+$/, '');

  /* ── CTA wiring ─────────────────────────────────────────── */
  const signupTargets = [
    document.getElementById('cta-signup'),
    document.getElementById('cta-signup-hero'),
    ...document.querySelectorAll('[data-cta="signup"]'),
  ].filter(Boolean);

  signupTargets.forEach((el) => { el.href = APP_URL + '/#signup'; });

  const signinEl = document.getElementById('cta-signin');
  if (signinEl) signinEl.href = APP_URL + '/';

  /* ── Mobile nav toggle ──────────────────────────────────── */
  const toggle = document.getElementById('nav-toggle');
  const links  = document.getElementById('nav-links');

  function closeMenu() {
    links.classList.remove('is-open');
    toggle.setAttribute('aria-expanded', 'false');
  }

  if (toggle && links) {
    toggle.addEventListener('click', () => {
      const open = links.classList.toggle('is-open');
      toggle.setAttribute('aria-expanded', String(open));
    });

    links.querySelectorAll('a').forEach((a) => {
      a.addEventListener('click', () => {
        if (links.classList.contains('is-open')) closeMenu();
      });
    });
  }
})();
