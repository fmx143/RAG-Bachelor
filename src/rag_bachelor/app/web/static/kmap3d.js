// Knowledge map (home) in 3D. Progressive enhancement: the SVG stays in the DOM as the data source
// and the fallback (no WebGL, script error); clicks are forwarded to its .gnode so app.js's focus card
// logic is reused as is.
import * as THREE from "./three.module.min.js";

const box = document.querySelector("[data-kmap]");
const svg = box?.querySelector(".o-graph");
if (svg && window.WebGLRenderingContext) {
  await document.fonts.ready; // labels are drawn once onto canvases
  try { init(); } catch (e) { console.warn("Carte 3D indisponible :", e); }
}

function init() {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); // throws without WebGL
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  const canvas = renderer.domElement;
  canvas.className = "kmap3d";
  canvas.setAttribute("aria-hidden", "true");

  const css = getComputedStyle(box);
  const px = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  const color = (c) => { // any CSS colour (oklch…) → THREE.Color, through a 1px canvas
    px.clearRect(0, 0, 1, 1); px.fillStyle = c; px.fillRect(0, 0, 1, 1);
    const [r, g, b] = px.getImageData(0, 0, 1, 1).data;
    return new THREE.Color().setRGB(r / 255, g / 255, b / 255, THREE.SRGBColorSpace);
  };
  const accent = color(css.getPropertyValue("--accent"));

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 760 / 300, 0.1, 200);
  camera.position.z = 30;
  scene.add(new THREE.AmbientLight(0xffffff, 0.7));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(6, 10, 12);
  scene.add(sun);
  const world = new THREE.Group();
  world.rotation.x = 0.25;
  scene.add(world);

  const hub = new THREE.Mesh(new THREE.SphereGeometry(0.7, 24, 16),
    new THREE.MeshStandardMaterial({ color: accent, emissive: accent, emissiveIntensity: 0.8 }));
  world.add(hub);

  const label = (text) => {
    const cv = document.createElement("canvas");
    cv.width = 512; cv.height = 96;
    const g = cv.getContext("2d");
    g.font = `600 40px ${css.getPropertyValue("--sans")}`;
    g.fillStyle = css.getPropertyValue("--text2");
    g.textAlign = "center"; g.textBaseline = "middle";
    g.fillText(text, 256, 48, 500);
    const map = new THREE.CanvasTexture(cv);
    map.colorSpace = THREE.SRGBColorSpace;
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map, depthWrite: false }));
    s.scale.set(6.4, 1.2, 1);
    return s;
  };

  // Nodes on a flattened Fibonacci sphere around the hub, sized like the SVG circles.
  const gnodes = [...svg.querySelectorAll(".gnode")];
  const n = gnodes.length, R = 9;
  const spheres = gnodes.map((el, i) => {
    const y = n > 1 ? 1 - (2 * (i + 0.5)) / n : 0, ring = Math.sqrt(1 - y * y), th = i * 2.39996;
    const pos = new THREE.Vector3(R * ring * Math.cos(th), R * y * 0.55, R * ring * Math.sin(th));
    const m = +el.dataset.mastery / 100;
    const c = color(`oklch(0.79 0.13 ${getComputedStyle(el).getPropertyValue("--h")})`);
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(+el.querySelector("circle").getAttribute("r") / 10, 32, 20),
      new THREE.MeshStandardMaterial({ color: c, emissive: c, emissiveIntensity: 0.15 + m * 0.4,
        roughness: 0.35, metalness: 0.1, transparent: true, opacity: 0.55 + m * 0.45 }));
    mesh.position.copy(pos);
    mesh.userData = { el, base: 0.15 + m * 0.4 };
    const tag = label(`${el.dataset.name} · ${el.dataset.mastery} %`);
    tag.position.copy(pos).add(new THREE.Vector3(0, -(mesh.geometry.parameters.radius + 0.9), 0));
    const spoke = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), pos]),
      new THREE.LineBasicMaterial({ color: c, transparent: true, opacity: 0.3 }));
    world.add(mesh, tag, spoke);
    return mesh;
  });

  // Cross-document links: curves bowed toward the hub, opacity ~ number of shared notions.
  const links = [...svg.querySelectorAll(".xlink")].map((el) => {
    const a = spheres[+el.dataset.a].position, b = spheres[+el.dataset.b].position;
    const mid = a.clone().add(b).multiplyScalar(0.5 * 0.65);
    const base = 0.25 + Math.min(parseFloat(el.style.strokeWidth) || 1, 5) / 10;
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(new THREE.QuadraticBezierCurve3(a, mid, b).getPoints(40)),
      new THREE.LineBasicMaterial({ color: accent, transparent: true, opacity: base }));
    line.userData = { el, base };
    world.add(line);
    return line;
  });

  // Drag rotates the map; a click without drag (raycast) focuses a node via the SVG's own handler.
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  const hit = (e) => {
    const r = canvas.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    return ray.intersectObjects(spheres)[0]?.object;
  };
  let drag = null;
  canvas.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, moved: 0 }; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener("pointermove", (e) => {
    if (!drag) { canvas.style.cursor = hit(e) ? "pointer" : ""; return; }
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    drag.moved += Math.abs(dx) + Math.abs(dy);
    world.rotation.y += dx * 0.008;
    world.rotation.x = Math.max(-1, Math.min(1, world.rotation.x + dy * 0.008));
    drag.x = e.clientX; drag.y = e.clientY;
  });
  canvas.addEventListener("pointerup", (e) => {
    if (drag && drag.moved < 5) hit(e)?.userData.el.dispatchEvent(new Event("click"));
    drag = null;
  });
  canvas.addEventListener("pointercancel", () => { drag = null; });

  const spin = matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 0.0015;
  const frame = () => {
    if (!drag) world.rotation.y += spin;
    const on = svg.querySelector(".gnode.on");
    for (const s of spheres) {
      const k = !on || s.userData.el === on ? 1 : 0.45;
      s.scale.setScalar(THREE.MathUtils.lerp(s.scale.x, s.userData.el === on ? 1.35 : 1, 0.12));
      s.material.emissiveIntensity = s.userData.el === on ? 0.9 : s.userData.base * k;
    }
    for (const l of links) l.material.opacity = l.userData.el.classList.contains("hot") ? 1 : on ? 0.12 : l.userData.base;
    renderer.render(scene, camera);
  };

  svg.after(canvas);
  box.classList.add("is-3d");
  new ResizeObserver(() => {
    const { clientWidth: w, clientHeight: h } = canvas;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }).observe(canvas);
  new IntersectionObserver(([e]) => renderer.setAnimationLoop(e.isIntersecting ? frame : null)).observe(canvas);
}
