(function () {
  "use strict";
  var saved = null;
  try { saved = localStorage.getItem("khanshoof_theme"); } catch (e) {}
  var theme = saved === "dark" || saved === "light"
    ? saved
    : (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = theme;
})();
