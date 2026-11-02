// Image occlusion editor (document page, "Masquage" tab): drag on the page image to draw
// rectangles (stored in % of the image), label or remove them in the list, and mirror them as
// JSON into the form's hidden `rects` field. Pointer events cover mouse, pen and touch.
(() => {
  const form = document.querySelector("[data-occ-edit]");
  if (!form) return;
  const stage = form.querySelector("[data-stage]");
  const list = form.querySelector("[data-list]");
  const field = form.querySelector("[name=rects]");
  const submit = form.querySelector("[data-submit]");
  const MAX = 30;
  const MIN = 0.5; // % of the page: below this a drag is just a click
  const rects = [];
  const round = (v) => Math.round(v * 100) / 100;

  const sync = () => {
    field.value = JSON.stringify(rects);
    submit.disabled = rects.length === 0;
  };

  const render = () => {
    stage.querySelectorAll(".occ-box").forEach((b) => b.remove());
    list.replaceChildren();
    rects.forEach((r, i) => {
      const box = document.createElement("i");
      box.className = "occ-box";
      box.textContent = String(i + 1);
      Object.assign(box.style, { left: `${r.x}%`, top: `${r.y}%`, width: `${r.w}%`, height: `${r.h}%` });
      stage.append(box);

      const li = document.createElement("li");
      const label = document.createElement("input");
      label.type = "text";
      label.maxLength = 80;
      label.value = r.label;
      label.placeholder = `Zone ${i + 1} : réponse attendue (facultatif)`;
      label.addEventListener("input", () => { r.label = label.value; sync(); });
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "btn btn-ghost btn-sm";
      rm.textContent = "Retirer";
      rm.addEventListener("click", () => { rects.splice(i, 1); render(); sync(); });
      li.append(label, rm);
      list.append(li);
    });
    sync();
  };

  let start = null;
  let preview = null;
  const point = (e) => {
    const b = stage.getBoundingClientRect();
    return {
      x: Math.min(100, Math.max(0, ((e.clientX - b.left) / b.width) * 100)),
      y: Math.min(100, Math.max(0, ((e.clientY - b.top) / b.height) * 100)),
    };
  };
  const span = (a, b) => ({
    x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y),
  });

  stage.addEventListener("pointerdown", (e) => {
    if (rects.length >= MAX || e.button > 0) return;
    start = point(e);
    stage.setPointerCapture(e.pointerId);
    preview = document.createElement("i");
    preview.className = "occ-box draft";
    stage.append(preview);
  });
  stage.addEventListener("pointermove", (e) => {
    if (!start) return;
    const s = span(start, point(e));
    Object.assign(preview.style, { left: `${s.x}%`, top: `${s.y}%`, width: `${s.w}%`, height: `${s.h}%` });
  });
  const finish = (e, keep) => {
    if (!start) return;
    const s = span(start, point(e));
    start = null;
    preview.remove();
    if (keep && s.w >= MIN && s.h >= MIN) {
      rects.push({ x: round(s.x), y: round(s.y), w: round(s.w), h: round(s.h), label: "" });
    }
    render();
  };
  stage.addEventListener("pointerup", (e) => finish(e, true));
  stage.addEventListener("pointercancel", (e) => finish(e, false));
  render();
})();
