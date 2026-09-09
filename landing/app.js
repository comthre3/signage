(function () {
  'use strict';

  const APP_URL = (window.APP_URL || 'https://app.khanshoof.com').replace(/\/+$/, '');

  function bindLangToggle() {
    const btn = document.getElementById('lang-toggle');
    if (!btn) return;
    btn.addEventListener('click', () => {
      const next = Khan.currentLocale() === 'en' ? 'ar' : 'en';
      Khan.setLocale(next);
      location.reload();
    });
  }

  async function bootI18n() {
    const locale = Khan.detectInitialLocale();
    await Khan.loadLocale(locale);
    Khan.applyTranslations(document);
    bindLangToggle();
  }

  bootI18n().catch(err => console.error('[boot] i18n failed', err));

  /* ── CTA wiring ─────────────────────────────────────────── */
  const signupTargets = [
    document.getElementById('cta-signup'),
    document.getElementById('cta-signup-hero'),
    ...document.querySelectorAll('[data-cta="signup"]'),
  ].filter(Boolean);
  signupTargets.forEach((el) => { el.href = APP_URL + '/#signup'; });

  const signinEl = document.getElementById('cta-signin');
  if (signinEl) signinEl.href = APP_URL + '/';

  /* ── Theme toggle ───────────────────────────────────────── */
  const themeBtn = document.getElementById('theme-toggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem('khanshoof_theme', next); } catch (e) {}
    });
  }

  /* ── Showcase: "Show zones" outlines the zones like the editor does ── */
  const zonesBtn = document.getElementById('showcase-zones-toggle');
  const showcaseGrid = document.getElementById('showcase-grid');
  if (zonesBtn && showcaseGrid) {
    zonesBtn.addEventListener('click', () => {
      const on = showcaseGrid.classList.toggle('zones-visible');
      zonesBtn.setAttribute('aria-pressed', String(on));
    });
  }

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
      a.addEventListener('click', () => { if (links.classList.contains('is-open')) closeMenu(); });
    });
  }
})();
