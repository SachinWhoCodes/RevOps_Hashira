// Apply the saved theme before the first paint so dark mode never flashes light.
// A separate file (not inline) because production's Content-Security-Policy blocks inline scripts.
(function () {
  var theme = "system";
  try {
    theme = localStorage.getItem("casefile-theme") || "system";
  } catch (e) {
    /* storage blocked: follow the system */
  }
  var dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  if (dark) document.documentElement.classList.add("dark");
})();
