// Range inputs mirror their value into the element named by data-output
// (replaces inline oninput handlers, which the CSP forbids).
document.addEventListener("input", (e) => {
  const out = e.target.dataset && e.target.dataset.output;
  if (out) document.getElementById(out).textContent = e.target.value;
});

// Document upload: one XHR per file (relative path kept), so no request grows past one PDF.
// Sequential on purpose: the server hashes, moves and queues each file before the next arrives.
// Picking files or a folder first shows a checklist: import and index, per file or per folder.
(() => {
  const btn = document.getElementById("upload-btn");
  if (!btn) return;
  const filesInput = document.getElementById("upload-files");
  const inputs = [filesInput, document.getElementById("upload-folder")];
  const bar = document.getElementById("upload-progress");
  const status = document.getElementById("upload-status");
  const log = document.getElementById("upload-log");
  const plan = document.getElementById("import-plan");
  const dest = document.getElementById("import-dest");
  let rows = []; // {file, path, imp, idx}: one per listed PDF

  const send = (file, relpath, index) =>
    new Promise((resolve) => {
      const body = new FormData();
      body.append("file", file);
      body.append("relpath", relpath);
      body.append("index", index ? "true" : "false");
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

  // ── Checklist ──
  const picked = () => inputs.flatMap((i) => [...i.files]).map((f) => [f, f.webkitRelativePath || f.name]);
  // Skip non-PDFs and hidden entries (.DS_Store, AppleDouble ._x.pdf): the server refuses them too.
  const isPdf = ([, p]) => p.toLowerCase().endsWith(".pdf") && !p.split("/").some((s) => s.startsWith("."));
  // Files before sub-folders at each level, so a folder row is followed by its own content only.
  const byPath = ([, a], [, b]) => {
    const x = a.split("/"), y = b.split("/");
    for (let i = 0; i < Math.min(x.length, y.length); i++) {
      const fx = i === x.length - 1, fy = i === y.length - 1;
      if (fx !== fy) return fx ? -1 : 1;
      const c = x[i].localeCompare(y[i], "fr", { numeric: true });
      if (c) return c;
    }
    return 0;
  };
  const box = (label, onchange) => {
    const c = document.createElement("input");
    c.type = "checkbox";
    c.checked = true;
    c.setAttribute("aria-label", label);
    c.addEventListener("change", onchange);
    return c;
  };
  const line = (depth, label, imp, idx) => {
    const name = document.createElement("span");
    name.textContent = label; // textContent: file names are never parsed as HTML
    name.style.paddingLeft = `${depth * 16}px`;
    const div = document.createElement("div");
    div.className = "plan-row";
    div.append(imp, idx, name);
    plan.append(div);
  };
  const under = (prefix) => rows.filter((r) => r.path.startsWith(prefix));
  const syncIdx = (r) => {
    r.idx.disabled = !r.imp.checked;
    if (r.idx.disabled) r.idx.checked = false;
  };
  const group = (prefix, depth, label) => {
    const imp = box(`Importer ${label}`, () =>
      under(prefix).forEach((r) => {
        r.imp.checked = imp.checked;
        syncIdx(r);
      })
    );
    const idx = box(`Indexer ${label}`, () =>
      under(prefix).forEach((r) => {
        if (!r.idx.disabled) r.idx.checked = idx.checked;
      })
    );
    line(depth, label, imp, idx);
  };

  const renderPlan = () => {
    const todo = picked().filter(isPdf).sort(byPath);
    plan.replaceChildren();
    rows = [];
    status.textContent = picked().length && !todo.length ? "Aucun PDF dans la sélection." : "";
    if (!todo.length) return;
    const hint = document.createElement("p");
    hint.className = "plan-hint";
    hint.textContent = "1ʳᵉ case : importer · 2ᵉ case : indexer";
    plan.append(hint);
    group("", 0, "Tout");
    const seen = new Set();
    for (const [file, path] of todo) {
      const parts = path.split("/");
      parts.slice(0, -1).forEach((seg, d) => {
        const prefix = parts.slice(0, d + 1).join("/") + "/";
        if (!seen.has(prefix)) {
          seen.add(prefix);
          group(prefix, d + 1, `📁 ${seg}`);
        }
      });
      const row = { file, path };
      row.imp = box(`Importer ${path}`, () => syncIdx(row));
      row.idx = box(`Indexer ${path}`, () => {});
      rows.push(row);
      line(parts.length, parts[parts.length - 1], row.imp, row.idx);
    }
  };

  const upload = async () => {
    const todo = rows.filter((r) => r.imp.checked);
    if (!todo.length) {
      status.textContent = "Rien à importer : coche au moins un PDF.";
      return;
    }
    // Subject first, then the optional folders typed in the destination field.
    const base = [btn.dataset.subject, ...dest.value.split("/").map((s) => s.trim()).filter(Boolean)].join("/");
    btn.disabled = true;
    log.replaceChildren();
    bar.max = todo.length;
    bar.value = 0;
    bar.hidden = false;
    let errors = 0;
    for (const [i, r] of todo.entries()) {
      status.textContent = `Envoi ${i + 1}/${todo.length} — ${r.path}`;
      const res = await send(r.file, `${base}/${r.path}`, r.idx.checked);
      if (!res.ok) errors++;
      const li = document.createElement("li");
      li.textContent = res.text; // textContent: the reply is never parsed as HTML
      log.append(li);
      bar.value = i + 1;
    }
    status.textContent = `${todo.length - errors}/${todo.length} traité(s)` + (errors ? ` · ${errors} erreur(s)` : "");
    inputs.forEach((i) => (i.value = ""));
    plan.replaceChildren();
    rows = [];
    btn.disabled = false;
    refreshList();
  };

  inputs.forEach((i) => i.addEventListener("change", renderPlan));
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
      renderPlan();
    });
  }
})();

// Subject picker (header): switching submits the form; "+ Nouveau sujet…" asks for a name first.
document.addEventListener("change", (e) => {
  const sel = e.target;
  if (!sel.matches || !sel.matches("[data-subject-select]")) return;
  if (sel.value === "") {
    const name = (prompt("Nom du nouveau sujet :") || "").trim();
    if (!name) {
      sel.value = sel.dataset.current; // cancelled: back to the active subject
      return;
    }
    sel.form.elements.name.value = name;
  }
  sel.form.submit();
});

// Mobile topbar: the nav scrolls horizontally, so bring the active tab into view.
document.querySelector(".nav-link.active")?.scrollIntoView({ inline: "center", block: "nearest" });

// Print button on a revision sheet (inline handlers are forbidden by the CSP).
document.addEventListener("click", (e) => {
  if (e.target.closest && e.target.closest("[data-print]")) window.print();
});

// Revision shortcuts: Space reveals the answer, 1-4 grade it, Enter-less "next" button gets focus.
document.addEventListener("keydown", (e) => {
  if (!document.getElementById("revision-content") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.target.closest && e.target.closest("input, textarea, select, summary, button")) return;
  const reveal = document.querySelector("#revision-content details.reveal:not([open])");
  if (e.key === " " && reveal) {
    e.preventDefault();
    reveal.open = true;
  } else if (e.key >= "1" && e.key <= "4") {
    document.querySelector(`#revision-content .gradebar .g${+e.key - 1}`)?.click();
  }
});
