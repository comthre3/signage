(function () {
  const ALLOWED = ["en", "ar"];
  let _strings = {};
  let _locale = "en";

  async function loadLocale(locale) {
    if (!ALLOWED.includes(locale)) locale = "en";
    const res = await fetch(`/i18n/${locale}.json`);
    if (!res.ok) {
      console.error("[i18n] failed to load", locale, res.status);
      return;
    }
    _strings = await res.json();
    _locale = locale;
    document.documentElement.lang = locale;
    document.documentElement.dir = locale === "ar" ? "rtl" : "ltr";
  }

  function t(key, fallback) {
    const v = _strings[key];
    if (v == null) {
      if (typeof console !== "undefined") console.warn("[i18n] missing key:", key);
      return fallback != null ? fallback : key;
    }
    return v;
  }

  // True when the loaded locale has a string for `key`. Lets callers prefer a
  // translation when one exists and fall back to something better than the
  // bare key (e.g. a backend message) when it does not.
  function has(key) { return _strings[key] != null; }

  function applyTranslations(root) {
    const r = root || document;
    r.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
    r.querySelectorAll("[data-i18n-placeholder]").forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
    r.querySelectorAll("[data-i18n-title]").forEach(el => { el.title = t(el.dataset.i18nTitle); });
    r.querySelectorAll("[data-i18n-aria-label]").forEach(el => { el.setAttribute("aria-label", t(el.dataset.i18nAriaLabel)); });
  }

  function setCookie(locale) {
    const host = location.hostname;
    const isProd = host.endsWith("khanshoof.com");
    const domainAttr = isProd ? "; domain=.khanshoof.com" : "";
    document.cookie = `khanshoof_lang=${locale}${domainAttr}; path=/; max-age=31536000; samesite=lax`;
  }

  function setLocale(locale) {
    if (!ALLOWED.includes(locale)) locale = "en";
    setCookie(locale);
    return locale;
  }

  // Precedence: the person's own explicit choice, then the organization's
  // default, then the browser. The cookie is only ever written by a language
  // toggle (here, on the landing page or the player), so it IS that explicit
  // choice. It used to lose to the org locale, which made the toggle a no-op
  // for everyone whose org was set to the other language -- every non-admin,
  // and admins whose org PATCH failed (402 on an expired subscription).
  function detectInitialLocale(orgLocale) {
    const m = document.cookie.match(/(?:^|; )khanshoof_lang=(en|ar)\b/);
    if (m) return m[1];
    if (ALLOWED.includes(orgLocale)) return orgLocale;
    const browser = (navigator.language || "en").slice(0, 2).toLowerCase();
    return browser === "ar" ? "ar" : "en";
  }

  function currentLocale() { return _locale; }

  window.Khan = { loadLocale, t, has, applyTranslations, setLocale, detectInitialLocale, currentLocale };
})();
