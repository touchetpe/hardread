// Applied before first paint: the saved theme choice ("light" | "dark"; absent = follow the system).
(function () {
  try {
    var t = localStorage.getItem("hardread.theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* storage blocked: follow the system */ }
})();
