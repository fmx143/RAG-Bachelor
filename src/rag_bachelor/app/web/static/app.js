// Range inputs mirror their value into the element named by data-output
// (replaces inline oninput handlers, which the CSP forbids).
document.addEventListener("input", (e) => {
  const out = e.target.dataset && e.target.dataset.output;
  if (out) document.getElementById(out).textContent = e.target.value;
});

// Chapter pickers (document page): choosing a chapter fills its form's page range;
// typing a page switches the picker to "Pages au choix…".
document.addEventListener("change", (e) => {
  const pick = e.target.closest && e.target.closest("[data-chapter-pick]");
  if (!pick) return;
  const opt = pick.selectedOptions[0];
  if (!opt || opt.hasAttribute("data-custom")) return;
  pick.form.elements.page_from.value = opt.dataset.from;
  pick.form.elements.page_to.value = opt.dataset.to;
});
document.addEventListener("input", (e) => {
  const form = e.target.form;
  const pick = form && form.querySelector("[data-chapter-pick]");
  if (!pick || !["page_from", "page_to"].includes(e.target.name)) return;
  pick.value = pick.querySelector("[data-custom]").value;
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
    const kind = document.querySelector('input[name="import-kind"]:checked');
    const folders = dest.value.split("/").map((s) => s.trim()).filter(Boolean);
    const base = [btn.dataset.subject, ...(kind ? [kind.value] : []), ...folders].join("/");
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

// Ask: Ctrl/Cmd+Enter in the question box submits the form.
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && e.target.id === "question") {
    e.preventDefault();
    e.target.form?.requestSubmit();
  }
});

// Progress heatmap: hovering a day shows its detail above the grid.
document.addEventListener("mouseover", (e) => {
  const c = e.target.closest && e.target.closest(".o-heatcol .c[data-tip]");
  const tip = document.getElementById("heat-tip");
  if (c && tip) tip.textContent = c.dataset.tip;
});

// Mind map: fold/unfold branches, zoom (inline handlers are forbidden by the CSP).
document.addEventListener("click", (e) => {
  const t = e.target.closest && e.target.closest("[data-mm-toggle]");
  if (t) t.parentElement.classList.toggle("closed");
  const all = e.target.closest && e.target.closest("[data-mm-all]");
  if (all) document.querySelectorAll(".mm-n").forEach((n) => n.classList.toggle("closed", all.dataset.mmAll === "close" && !!n.querySelector(".mm-kids") && !n.parentElement.matches("[data-mm-scale]")));
  const z = e.target.closest && e.target.closest("[data-mm-zoom]");
  const scale = document.querySelector("[data-mm-scale]");
  if (z && scale) {
    const v = Math.min(1.4, Math.max(0.6, (parseFloat(scale.dataset.z) || 1) + parseFloat(z.dataset.mmZoom)));
    scale.dataset.z = v;
    scale.style.transform = `scale(${v})`;
    document.querySelector("[data-mm-pct]").textContent = `${Math.round(v * 100)} %`;
  }
});
document.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches("[data-mm-toggle]")) {
    e.preventDefault();
    e.target.parentElement.classList.toggle("closed");
  }
});

// Audio lesson player: custom controls over a hidden <audio>.
document.querySelectorAll("[data-player]").forEach((box) => {
  const au = box.querySelector("[data-audio]");
  const bars = [...box.querySelectorAll("[data-wave] span")];
  const icon = box.querySelector("[data-icon]");
  const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  box.querySelector("[data-play]").addEventListener("click", () => (au.paused ? au.play() : au.pause()));
  au.addEventListener("play", () => icon.setAttribute("d", "M6 5h4v14H6zM14 5h4v14h-4z"));
  au.addEventListener("pause", () => icon.setAttribute("d", "M8 5v14l11-7z"));
  // Transcript lines: a click seeks to the line, playback highlights the current one.
  const lines = [...document.querySelectorAll("[data-line]")];
  let current = null;
  lines.forEach((l) => l.addEventListener("click", () => {
    au.currentTime = parseFloat(l.dataset.start) || 0;
    au.play();
  }));
  au.addEventListener("timeupdate", () => {
    box.querySelector("[data-cur]").textContent = fmt(au.currentTime);
    const k = au.duration ? Math.round((au.currentTime / au.duration) * bars.length) : 0;
    bars.forEach((b, i) => b.classList.toggle("on", i < k));
    const line = lines.findLast((l) => parseFloat(l.dataset.start) <= au.currentTime + 0.05) || null;
    if (line === current) return;
    if (current) current.classList.remove("on");
    current = line;
    if (line) {
      line.classList.add("on");
      if (!au.paused) line.scrollIntoView({ block: "nearest" });
    }
  });
  box.querySelector("[data-wave]").addEventListener("click", (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    if (au.duration) au.currentTime = ((e.clientX - r.left) / r.width) * au.duration;
  });
  box.querySelectorAll("[data-speed]").forEach((b) => b.addEventListener("click", () => {
    au.playbackRate = parseFloat(b.dataset.speed);
    box.querySelectorAll("[data-speed]").forEach((x) => x.classList.toggle("on", x === b));
  }));
});

// Citation pills: on a sheet they swap the sticky page preview, in an answer they open the matching source card.
document.addEventListener("click", (e) => {
  const c = e.target.closest && e.target.closest("a.cite");
  if (!c) return;
  const img = document.querySelector("[data-prev-img]");
  if (img) {
    e.preventDefault();
    img.src = img.dataset.base + c.dataset.page;
    document.querySelector("[data-prev-page]").textContent = c.dataset.page;
    document.querySelectorAll("a.cite").forEach((x) => x.classList.toggle("on", x === c));
    return;
  }
  e.preventDefault();
  const srcs = [...document.querySelectorAll(".o-src")].filter((d) => d.dataset.src === c.dataset.src || d.dataset.src.endsWith("/" + c.dataset.src));
  const card = srcs.find((d) => d.dataset.page === c.dataset.page) || srcs[0];
  if (!card) return;
  card.open = true;
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  card.classList.remove("flash");
  void card.offsetWidth;
  card.classList.add("flash");
});

// Knowledge map (home): click a node or legend chip to focus a document and highlight its links.
document.querySelectorAll("[data-kmap]").forEach((box) => {
  const nodes = [...box.querySelectorAll(".gnode")];
  const chips = [...box.querySelectorAll("[data-focus]")];
  const card = box.querySelector(".kfocus");
  const svg = box.querySelector(".o-graph");
  const show = (i) => {
    const on = box.querySelector(".gnode.on")?.dataset.i === String(i);
    nodes.forEach((n) => n.classList.toggle("on", !on && n.dataset.i === String(i)));
    chips.forEach((c) => c.classList.toggle("on", !on && c.dataset.focus === String(i)));
    svg.classList.toggle("focus", !on);
    box.querySelectorAll(".xlink").forEach((l) => l.classList.toggle("hot", !on && (l.dataset.a === String(i) || l.dataset.b === String(i))));
    card.hidden = on;
    if (on) return;
    const n = nodes[i], d = n.dataset;
    card.style.setProperty("--c", `oklch(0.79 0.13 ${getComputedStyle(n).getPropertyValue("--h")})`);
    card.querySelector("[data-f=name]").textContent = d.name;
    card.querySelector("[data-f=file]").textContent = d.file;
    card.querySelector("[data-f=mastery]").textContent = `${d.mastery} %`;
    card.querySelector(".trk i").style.width = `${d.mastery}%`;
    card.querySelector("[data-f=meta]").textContent = d.meta;
    card.querySelector("[data-f=href]").href = d.href;
  };
  nodes.forEach((n) => {
    n.addEventListener("click", () => show(+n.dataset.i));
    n.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); show(+n.dataset.i); } });
  });
  chips.forEach((c) => c.addEventListener("click", () => show(+c.dataset.focus)));
});

// Phone menu: the sidebar slides in as a drawer (button, backdrop, Escape or a link close it).
(() => {
  const btn = document.querySelector(".menu-btn");
  const backdrop = document.querySelector(".menu-backdrop");
  if (!btn || !backdrop) return;
  const set = (open) => {
    document.body.classList.toggle("menu-open", open);
    btn.setAttribute("aria-expanded", String(open));
    btn.setAttribute("aria-label", open ? "Fermer le menu" : "Ouvrir le menu");
    backdrop.hidden = !open;
  };
  document.querySelectorAll("[data-menu-toggle]").forEach((el) =>
    el.addEventListener("click", () => set(!document.body.classList.contains("menu-open")))
  );
  document.querySelectorAll("#sidenav a").forEach((a) => a.addEventListener("click", () => set(false)));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.body.classList.contains("menu-open")) {
      set(false);
      btn.focus();
    }
  });
})();
