const ALLOWED = ["en", "ar"];
let _strings = {};
let _locale = "en";

export async function loadLocale(locale) {
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

export function t(key, fallback) {
  const v = _strings[key];
  if (v == null) {
    if (typeof console !== "undefined") console.warn("[i18n] missing key:", key);
    return fallback != null ? fallback : key;
  }
  return v;
}

export function applyTranslations(root) {
  const r = root || document;
  r.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  r.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  r.querySelectorAll("[data-i18n-title]").forEach(el => {
    el.title = t(el.dataset.i18nTitle);
  });
  r.querySelectorAll("[data-i18n-aria-label]").forEach(el => {
    el.setAttribute("aria-label", t(el.dataset.i18nAriaLabel));
  });
}

function setCookie(locale) {
  const host = location.hostname;
  const isProd = host.endsWith("khanshoof.com");
  const domainAttr = isProd ? "; domain=.khanshoof.com" : "";
  document.cookie = `khanshoof_lang=${locale}${domainAttr}; path=/; max-age=31536000; samesite=lax`;
}

export function setLocale(locale) {
  if (!ALLOWED.includes(locale)) locale = "en";
  setCookie(locale);
  return locale;
}

export function detectInitialLocale(orgLocale) {
  if (ALLOWED.includes(orgLocale)) return orgLocale;
  const m = document.cookie.match(/(?:^|; )khanshoof_lang=(en|ar)\b/);
  if (m) return m[1];
  const browser = (navigator.language || "en").slice(0, 2).toLowerCase();
  return browser === "ar" ? "ar" : "en";
}

export function currentLocale() {
  return _locale;
}
