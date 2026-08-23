// Mobile sidebar toggle — no framework, plain DOM (see CLAUDE.md: no CDN deps).
(() => {
  const btn = document.getElementById("nav-toggle-btn");
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.getElementById("nav-backdrop");
  if (!btn || !sidebar || !backdrop) return;

  const close = () => {
    sidebar.classList.remove("open");
    backdrop.hidden = true;
    btn.setAttribute("aria-expanded", "false");
  };
  const open = () => {
    sidebar.classList.add("open");
    backdrop.hidden = false;
    btn.setAttribute("aria-expanded", "true");
  };

  btn.addEventListener("click", () => {
    sidebar.classList.contains("open") ? close() : open();
  });
  backdrop.addEventListener("click", close);
  sidebar.querySelectorAll(".nav-link").forEach((link) => link.addEventListener("click", close));

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && sidebar.classList.contains("open")) {
      close();
      btn.focus();
    }
  });
})();
