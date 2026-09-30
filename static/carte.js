const SVG_NS = "http://www.w3.org/2000/svg";
const CX = 200, CY = 200, R = 165;   // rayon = horizon (0°), centre = zénith (90°)

const svg = document.getElementById("sky");
const form = document.getElementById("carte-form");
const villeEl = document.getElementById("ville");
const btn = document.getElementById("carte-btn");
const infosEl = document.getElementById("infos");

function node(tag, attrs, text) {
  const n = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;   // jamais innerHTML
  return n;
}

// Vue « on regarde le ciel » : nord en haut, est à gauche.
function project(az, alt) {
  const r = R * (90 - alt) / 90;
  const a = (az * Math.PI) / 180;
  return [CX - r * Math.sin(a), CY - r * Math.cos(a)];
}

function drawGrid() {
  svg.replaceChildren();
  [0, 30, 60].forEach((alt) => {
    const r = R * (90 - alt) / 90;
    svg.appendChild(node("circle", { cx: CX, cy: CY, r, class: "grid" }));
    if (alt > 0) svg.appendChild(node("text", { x: CX + 4, y: CY - r + 11, class: "tick" }, `${alt}°`));
  });
  svg.appendChild(node("line", { x1: CX, y1: CY - R, x2: CX, y2: CY + R, class: "grid" }));
  svg.appendChild(node("line", { x1: CX - R, y1: CY, x2: CX + R, y2: CY, class: "grid" }));
  [["N", 0], ["E", 90], ["S", 180], ["O", 270]].forEach(([label, az]) => {
    const [x, y] = project(az, -9);
    svg.appendChild(node("text", { x, y: y + 5, "text-anchor": "middle", class: "cardinal" }, label));
  });
}

function segments(points) {
  const out = [];
  points.forEach((p, i) => {
    if (i === 0) return;
    out.push({ a: points[i - 1], b: p, visible: p.visible && points[i - 1].visible });
  });
  return out;
}

function drawPass(passage) {
  const pts = passage.points;
  segments(pts).forEach(({ a, b, visible }) => {
    const [x1, y1] = project(a.az, a.alt);
    const [x2, y2] = project(b.az, b.alt);
    svg.appendChild(node("line", { x1, y1, x2, y2, class: visible ? "track vis" : "track" }));
  });

  const first = pts[0], last = pts[pts.length - 1];
  const top = pts.reduce((m, p) => (p.alt > m.alt ? p : m), pts[0]);
  [[first, "début", "start"], [top, `max ${Math.round(top.alt)}°`, "top"], [last, "fin", "end"]]
    .forEach(([p, label, cls]) => {
      const [x, y] = project(p.az, p.alt);
      svg.appendChild(node("circle", { cx: x, cy: y, r: 4, class: `dot ${cls}` }));
      svg.appendChild(node("text", { x: x + 7, y: y - 6, class: "label" }, `${label} ${p.t.slice(0, 5)}`));
    });
}

async function load() {
  btn.disabled = true;
  infosEl.textContent = "Calcul de la trajectoire…";
  try {
    const res = await fetch(`/api/trajectoire-iss?ville=${encodeURIComponent(villeEl.value.trim())}`);
    const data = await res.json();
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Erreur ${res.status}`);
    const p = data.passage;
    drawGrid();
    drawPass(p);
    const visibilite = p.visible_oeil_nu ? "visible à l'œil nu" : `non visible (${p.raison_non_visible})`;
    infosEl.textContent =
      `${data.ville} · ${p.debut} → ${p.fin.slice(11)} · hauteur max ${p.hauteur_max_deg}° · ${p.direction} · ${visibilite}`;
  } catch (err) {
    drawGrid();
    infosEl.textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!btn.disabled) load();
});

drawGrid();
if (villeEl.value.trim()) load();
