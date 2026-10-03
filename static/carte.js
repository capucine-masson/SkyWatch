// Carte du ciel : tiroir de la page de chat (#sky-drawer) ou page autonome /carte.
// Enveloppé dans une fonction : app.js déclare aussi `form`, `villeEl`… dans la même page.
(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const CX = 200, CY = 200, R = 165;   // rayon = horizon (0°), centre = zénith (90°)

  const svg = document.getElementById("sky");
  const form = document.getElementById("carte-form");      // page /carte uniquement
  const villeEl = document.getElementById("ville");
  const btn = document.getElementById("carte-btn");
  const infosEl = document.getElementById("infos");
  const drawer = document.getElementById("sky-drawer");    // page de chat uniquement
  const toggle = document.getElementById("sky-toggle");
  const closeBtn = document.getElementById("sky-close");
  const toggleLabel = toggle && toggle.querySelector(".sky-toggle-label");
  let lastVille = null;   // ville de la dernière carte affichée dans le tiroir

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

  // Générateur pseudo-aléatoire à graine fixe : les étoiles de fond ne bougent pas d'un tracé à l'autre.
  function mulberry32(seed) {
    return () => {
      seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function drawGrid() {
    svg.replaceChildren();

    const defs = node("defs");
    const glow = node("filter", { id: "glow", filterUnits: "userSpaceOnUse", x: 0, y: 0, width: 400, height: 400 });
    glow.appendChild(node("feGaussianBlur", { stdDeviation: "3", result: "blur" }));
    const merge = node("feMerge");
    merge.appendChild(node("feMergeNode", { in: "blur" }));
    merge.appendChild(node("feMergeNode", { in: "SourceGraphic" }));
    glow.appendChild(merge);
    defs.appendChild(glow);
    svg.appendChild(defs);

    const rand = mulberry32(42);
    for (let i = 0; i < 70; i++) {
      const r = (R - 6) * Math.sqrt(rand());
      const a = rand() * 2 * Math.PI;
      svg.appendChild(node("circle", {
        cx: CX + r * Math.cos(a), cy: CY + r * Math.sin(a), r: 0.5 + rand() * 0.9,
        class: "bgstar", style: `animation-delay:${(-rand() * 6).toFixed(2)}s`,
      }));
    }

    [0, 30, 60].forEach((alt) => {
      const r = R * (90 - alt) / 90;
      svg.appendChild(node("circle", { cx: CX, cy: CY, r, class: "grid" }));
      if (alt > 0) svg.appendChild(node("text", { x: CX + 4, y: CY - r + 11, class: "tick" }, `${alt}°`));
    });
    svg.appendChild(node("line", { x1: CX, y1: CY - R, x2: CX, y2: CY + R, class: "grid" }));
    svg.appendChild(node("line", { x1: CX - R, y1: CY, x2: CX + R, y2: CY, class: "grid" }));

    // graduations tous les 15° sur l'horizon
    for (let az = 0; az < 360; az += 15) {
      const long = az % 30 === 0;
      const [x1, y1] = project(az, 0);
      const [x2, y2] = project(az, long ? 5 : 2.5);
      svg.appendChild(node("line", { x1, y1, x2, y2, class: "rim" }));
    }

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
    const segs = segments(pts);
    // un seul groupe lumineux pour les segments visibles (un filtre par segment serait coûteux)
    const dim = svg.appendChild(node("g"));
    const lit = svg.appendChild(node("g", { class: "lit" }));
    segs.forEach(({ a, b, visible }, i) => {
      const [x1, y1] = project(a.az, a.alt);
      const [x2, y2] = project(b.az, b.alt);
      (visible ? lit : dim).appendChild(node("line", {
        x1, y1, x2, y2, class: visible ? "track vis" : "track",
        style: `animation-delay:${Math.round((i / segs.length) * 900)}ms`,   // la trace se dessine
      }));
    });

    const first = pts[0], last = pts[pts.length - 1];
    const top = pts.reduce((m, p) => (p.alt > m.alt ? p : m), pts[0]);
    [[first, "apparaît", "start"], [top, "sommet", "top"], [last, "disparaît", "end"]]
      .forEach(([p, label, cls]) => {
        const [x, y] = project(p.az, p.alt);
        if (cls === "top") svg.appendChild(node("circle", { cx: x, cy: y, r: 4, class: "ping" }));
        svg.appendChild(node("circle", { cx: x, cy: y, r: 4, class: `dot ${cls}` }));
        const droite = x > CX + 20;   // évite que l'étiquette sorte du cadre
        svg.appendChild(node("text", {
          x: droite ? x - 7 : x + 7, y: y - 6, "text-anchor": droite ? "end" : "start", class: "label",
        }, `${label} ${p.t.slice(0, 5)}`));
      });
  }

  const DIRECTIONS = {
    N: "nord", NNE: "nord-nord-est", NE: "nord-est", ENE: "est-nord-est", E: "est", ESE: "est-sud-est",
    SE: "sud-est", SSE: "sud-sud-est", S: "sud", SSO: "sud-sud-ouest", SO: "sud-ouest", OSO: "ouest-sud-ouest",
    O: "ouest", ONO: "ouest-nord-ouest", NO: "nord-ouest", NNO: "nord-nord-ouest",
  };
  const mot = (code) => DIRECTIONS[code] || code;

  // Hauteur en mots : un poing tendu à bout de bras couvre environ 10° de ciel.
  function hauteurEnMots(deg) {
    if (deg < 15) return "très bas dans le ciel (à peu près un poing tendu au-dessus de l'horizon)";
    if (deg < 35) return "assez bas (deux à trois poings tendus au-dessus de l'horizon)";
    if (deg < 60) return "à mi-hauteur dans le ciel";
    return "très haut, presque au-dessus de votre tête";
  }

  function dureeEnMots(s) {
    if (s < 60) return `${s} secondes`;
    const min = Math.round(s / 60);
    return min === 1 ? "environ 1 minute" : `environ ${min} minutes`;
  }

  function jourEnMots(debut) {   // "2026-10-01 19:20" -> "Jeudi 1 octobre"
    const [y, m, d] = debut.slice(0, 10).split("-").map(Number);
    const txt = new Date(y, m - 1, d).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
    return txt.charAt(0).toUpperCase() + txt.slice(1);
  }

  function ligne(tag, className, text) {
    const n = document.createElement(tag);
    n.className = className;
    n.textContent = text;   // jamais innerHTML
    return n;
  }

  // Résumé du passage en phrases simples (chaîne = message d'attente ou d'erreur).
  function setInfos(content) {
    infosEl.replaceChildren();
    if (typeof content === "string") {
      infosEl.appendChild(ligne("p", "resume-msg", content));
      return;
    }
    const { ville, passage: p } = content;
    const dir = /de (\S+) vers (\S+)/.exec(p.direction);
    infosEl.appendChild(ligne("p", "resume-when", `${jourEnMots(p.debut)} · ${p.debut.slice(11)}`));
    infosEl.appendChild(ligne("p", "resume-text",
      `La Station spatiale internationale (ISS) passera au-dessus de ${ville}. ` +
      (dir ? `Elle apparaîtra du côté ${mot(dir[1])}, puis disparaîtra du côté ${mot(dir[2])}, ` : "Elle traversera le ciel ") +
      `en ${dureeEnMots(p.duree_s)}.`));
    infosEl.appendChild(ligne("p", "resume-text",
      `Au plus haut, elle sera ${hauteurEnMots(p.hauteur_max_deg)}, du côté ${mot(p.direction_hauteur_max)}.`));
    infosEl.appendChild(ligne("p", p.visible_oeil_nu ? "resume-badge ok" : "resume-badge no",
      p.visible_oeil_nu ? "Visible à l'œil nu" : `Difficile à voir : ${p.raison_non_visible}`));
  }

  async function load() {
    btn.disabled = true;
    lastVille = villeEl.value.trim();
    setInfos("Calcul de la trajectoire…");
    try {
      const res = await fetch(`/api/trajectoire-iss?ville=${encodeURIComponent(lastVille)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Erreur ${res.status}`);
      const p = data.passage;
      drawGrid();
      drawPass(p);
      setInfos({ ville: data.ville, passage: p });
    } catch (err) {
      drawGrid();
      setInfos(err.message);
    } finally {
      btn.disabled = false;
    }
  }

  // ---- Tiroir (page de chat) ----
  function setOpen(open) {
    document.body.classList.toggle("sky-open", open);
    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-label", open ? "Replier la carte du ciel" : "Ouvrir la carte du ciel");
    toggleLabel.textContent = open ? "replier" : "carte du ciel";
    drawer.inert = !open;   // hors focus clavier / lecteur d'écran quand il est replié
    if (open && lastVille !== villeEl.value.trim()) load();
  }

  drawGrid();
  if (drawer) {
    toggle.addEventListener("click", () => setOpen(!document.body.classList.contains("sky-open")));
    closeBtn.addEventListener("click", () => { setOpen(false); toggle.focus(); });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && document.body.classList.contains("sky-open")) {
        setOpen(false);
        toggle.focus();
      }
    });
    btn.addEventListener("click", () => { if (!btn.disabled) load(); });
  } else {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!btn.disabled) load();
    });
    if (villeEl.value.trim()) load();
  }
})();
