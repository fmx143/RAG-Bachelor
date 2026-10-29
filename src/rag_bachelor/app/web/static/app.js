// Range inputs mirror their value into the element named by data-output
// (replaces inline oninput handlers, which the CSP forbids).
document.addEventListener("input", (e) => {
  const out = e.target.dataset && e.target.dataset.output;
  if (out) document.getElementById(out).textContent = e.target.value;
});

// Document upload: one XHR per file (relative path kept), so no request grows past one PDF.
// Sequential on purpose: the server hashes, moves and queues each file before the next arrives.
(() => {
  const btn = document.getElementById("upload-btn");
  if (!btn) return;
  const filesInput = document.getElementById("upload-files");
  const inputs = [filesInput, document.getElementById("upload-folder")];
  const bar = document.getElementById("upload-progress");
  const status = document.getElementById("upload-status");
  const log = document.getElementById("upload-log");

  const send = (file, relpath) =>
    new Promise((resolve) => {
      const body = new FormData();
      body.append("file", file);
      body.append("relpath", relpath);
      const xhr = new XMLHttpRequest(); // not fetch: Firefox multipart bug, see htmx.min.js
      xhr.open("POST", "/docs/upload");
      xhr.onload = () => {
        // An expired session answers with a redirect the XHR follows to the login page.
        const expired = xhr.responseURL.includes("/login");
        resolve({
          ok: xhr.status < 400 && !expired,
          text: expired ? "Session expirée, reconnecte-toi." : xhr.responseText,
        });
      };
      xhr.onerror = () => resolve({ ok: false, text: `${relpath} : erreur réseau` });
      xhr.send(body);
    });

  const refreshList = () =>
    fetch("/docs/index/status")
      .then((r) => r.text())
      .then((html) => {
        const list = document.getElementById("doc-list");
        list.innerHTML = html; // same-origin fragment, Jinja-escaped (same trust as htmx swaps)
        htmx.processAll(list);
      });

  const upload = async () => {
    const picked = inputs.flatMap((i) => [...i.files]).map((f) => [f, f.webkitRelativePath || f.name]);
    // Skip non-PDFs and hidden entries (.DS_Store, AppleDouble ._x.pdf): the server refuses them too.
    const todo = picked.filter(
      ([, p]) => p.toLowerCase().endsWith(".pdf") && !p.split("/").some((s) => s.startsWith("."))
    );
    if (!todo.length) {
      status.textContent = "Aucun PDF sélectionné.";
      return;
    }
    btn.disabled = true;
    log.replaceChildren();
    bar.max = todo.length;
    bar.value = 0;
    bar.hidden = false;
    let errors = 0;
    for (const [i, [file, relpath]] of todo.entries()) {
      status.textContent = `Envoi ${i + 1}/${todo.length} — ${relpath}`;
      const res = await send(file, relpath);
      if (!res.ok) errors++;
      const li = document.createElement("li");
      li.textContent = res.text; // textContent: the reply is never parsed as HTML
      log.append(li);
      bar.value = i + 1;
    }
    status.textContent =
      `${todo.length - errors}/${todo.length} traité(s)` +
      (picked.length > todo.length ? ` · ${picked.length - todo.length} ignoré(s) (non-PDF)` : "") +
      (errors ? ` · ${errors} erreur(s)` : "");
    inputs.forEach((i) => (i.value = ""));
    btn.disabled = false;
    refreshList();
  };

  btn.addEventListener("click", upload);

  // Drag & drop of files onto the drop zone. ponytail: dropped folders are not walked
  // (needs webkitGetAsEntry recursion); use the folder picker for those.
  const zone = document.getElementById("drop-zone");
  if (zone) {
    ["dragenter", "dragover"].forEach((ev) =>
      zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("over"); })
    );
    ["dragleave", "drop"].forEach((ev) =>
      zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("over"); })
    );
    zone.addEventListener("drop", (e) => {
      filesInput.files = e.dataTransfer.files;
      upload();
    });
  }
})();

// Mobile topbar: the nav scrolls horizontally, so bring the active tab into view.
document.querySelector(".nav-link.active")?.scrollIntoView({ inline: "center", block: "nearest" });

// Print button on a revision sheet (inline handlers are forbidden by the CSP).
document.addEventListener("click", (e) => {
  if (e.target.closest && e.target.closest("[data-print]")) window.print();
});
