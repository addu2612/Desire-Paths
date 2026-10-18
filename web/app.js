/* Desire Paths UI */
const DAY = 86400;
const TAU = (120 * DAY) / Math.LN2; // same half-life as the backend
// Colors come from the CSS tokens so the canvas and the panels can never drift apart.
const css = getComputedStyle(document.documentElement);
const tok = (name) => css.getPropertyValue(name).trim();
const C = {
  clay: tok("--clay"), fg: tok("--fg"), muted: tok("--muted"), accent: tok("--accent"),
  teal: tok("--teal"), groove: tok("--groove"), risk: tok("--risk"),
  dark: "rgba(163,177,198,", light: "rgba(255,255,255,",
};

const S = {
  repo: null, repos: [], data: null, fg: null,
  T: null, tMin: 0, tMax: 0,
  layers: { blueprint: true, worn: true, collab: false },
  hl: null, // {nodes:Set, links:Set}
  paved: new Set(),
  history: [], busy: false, playing: false,
  maxScore: 1, hoverNode: null, planFade: 1,
};

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const linkKey = (a, b) => `${a}|${b}`;
const idOf = (x) => (typeof x === "object" ? x.id : x);
const fmtMonth = (t) => new Date(t * 1000).toLocaleDateString("en", { month: "short", year: "numeric" });
const ago = (t, now) => {
  if (!t) return "never";
  const d = Math.floor(((now ?? Date.now() / 1000) - t) / DAY);
  return d < 1 ? "today" : d < 60 ? `${d}d ago` : `${Math.floor(d / 30)}mo ago`;
};

/* ---------- time-dependent wear ---------- */
function wearAt(link, T) {
  let s = 0, last = null;
  for (const [t, w] of link.events) {
    if (t > T) break;
    s += w * Math.exp(-(T - t) / TAU);
    last = t;
  }
  return { s, last };
}

function recomputeWear() {
  const T = S.T;
  const personScore = new Map();
  const areaHeat = new Map();
  const personLast = new Map();
  let max = 0.001;
  for (const l of S.data.links) {
    if (l.kind !== "worn") continue;
    const { s, last } = wearAt(l, T);
    l._s = s; l._last = last;
    max = Math.max(max, s);
    const p = idOf(l.source), a = idOf(l.target);
    personScore.set(p, (personScore.get(p) || 0) + s);
    if (last) personLast.set(p, Math.max(personLast.get(p) || 0, last));
    areaHeat.set(a, (areaHeat.get(a) || 0) + s);
  }
  S.maxScore = max;
  let maxP = 0.001, maxA = 0.001;
  personScore.forEach((v) => (maxP = Math.max(maxP, v)));
  areaHeat.forEach((v) => (maxA = Math.max(maxA, v)));
  for (const n of S.data.nodes) {
    if (n.kind === "person") { n._s = (personScore.get(n.id) || 0) / maxP; n._last = personLast.get(n.id) || 0; }
    if (n.kind === "area") n._heat = (areaHeat.get(n.id) || 0) / maxA;
  }
}

/* ---------- drawing ---------- */
function isHl(id) { return !S.hl || S.hl.nodes.has(id); }
function linkHl(l) {
  if (!S.hl) return false;
  const a = idOf(l.source), b = idOf(l.target);
  return S.hl.links.has(linkKey(a, b)) || S.hl.links.has(linkKey(b, a));
}
function linkRisk(l) {
  return !!(S.hl && S.hl.risk && S.hl.risk.has(linkKey(idOf(l.source), idOf(l.target))));
}

function strokeLine(ctx, a, b, dx = 0, dy = 0) {
  ctx.beginPath();
  ctx.moveTo(a.x + dx, a.y + dy);
  ctx.lineTo(b.x + dx, b.y + dy);
  ctx.stroke();
}

// A desire path is a groove cut into the clay: dark rim on the top-left wall, light rim on the
// bottom-right, the floor in between. More work = wider and deeper; old paths grow shallow.
function drawGroove(ctx, a, b, width, depth, scale, fill) {
  const o = (0.8 + depth * 1.6) / scale;
  ctx.lineCap = "round";
  ctx.lineWidth = width;
  ctx.strokeStyle = "rgba(120,135,158," + (0.35 + 0.6 * depth) + ")"; // shadowed upper wall
  strokeLine(ctx, a, b, -o, -o);
  ctx.strokeStyle = C.light + (0.7 + 0.3 * depth) + ")";            // lit lower wall
  strokeLine(ctx, a, b, o, o);
  ctx.globalAlpha *= 0.55 + 0.45 * depth;
  ctx.strokeStyle = fill || C.groove;                                 // the worn floor
  ctx.lineWidth = Math.max(width - 1.2 * o, width * 0.55);
  strokeLine(ctx, a, b);
}

function drawLink(l, ctx, scale) {
  const a = l.source, b = l.target;
  if (a.x == null || b.x == null) return;
  const hl = linkHl(l);
  ctx.save();
  ctx.globalAlpha = S.hl && !hl ? 0.22 : 1;
  if (l.kind === "tree") {
    ctx.strokeStyle = "rgba(61,72,82,0.10)";
    ctx.lineWidth = 0.8 / scale;
    strokeLine(ctx, a, b);
  } else if (l.kind === "official") {
    ctx.setLineDash([5 / scale, 4 / scale]);
    ctx.strokeStyle = C.accent;
    ctx.globalAlpha *= hl ? 1 : (l.coverage >= 0.5 ? 0.42 : 0.2) * S.planFade;
    ctx.lineWidth = (hl ? 2 : 1) / scale;
    strokeLine(ctx, a, b);
  } else if (l.kind === "collab") {
    ctx.strokeStyle = hl ? C.fg : "rgba(107,114,128,0.32)";
    ctx.lineWidth = (hl ? 1.8 : 0.5 + Math.min(l.n, 30) / 30) / scale;
    strokeLine(ctx, a, b);
  } else if (l.kind === "worn") {
    const rel = Math.sqrt(l._s / S.maxScore);
    const age = l._last ? (S.T - l._last) / DAY : 999;
    const fresh = Math.max(0.1, Math.exp(-age / 100));
    const depth = Math.min(1, rel * (0.4 + 0.6 * fresh));
    const width = (2 + rel * 8) / scale;
    const paved = S.paved.has(linkKey(idOf(a), idOf(b)));
    const risk = linkRisk(l);
    drawGroove(ctx, a, b, width, hl ? 1 : depth, scale, risk ? C.risk : paved ? C.accent : null);
    if (hl && !paved && !risk) {
      ctx.strokeStyle = C.teal;
      ctx.lineWidth = Math.max(1.4 / scale, width * 0.38);
      strokeLine(ctx, a, b);
    }
  } else if (l.kind === "path") {
    drawGroove(ctx, a, b, 4 / scale, 1, scale, C.teal);
  } else if (l.kind === "risk") {
    drawGroove(ctx, a, b, 4 / scale, 1, scale, C.risk);
  }
  ctx.restore();
}

function label(ctx, text, x, y, size, weight, color, family, align = "center") {
  ctx.font = `${weight} ${size}px ${family}`;
  ctx.textAlign = align;
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  ctx.lineWidth = size * 0.42;
  ctx.strokeStyle = C.clay; // a clay halo keeps labels legible over grooves
  ctx.strokeText(text, x, y);
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
}

// People are pebbles pressed out of the surface. Canvas shadow offsets are in screen pixels,
// so the lighting stays constant at every zoom level, like the rest of the UI.
function pebble(ctx, x, y, r, lift) {
  ctx.save();
  ctx.fillStyle = C.clay;
  ctx.shadowColor = C.dark + "0.75)";
  ctx.shadowOffsetX = ctx.shadowOffsetY = 2 + lift * 3;
  ctx.shadowBlur = 4 + lift * 6;
  ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
  ctx.shadowColor = C.light + "0.9)";
  ctx.shadowOffsetX = ctx.shadowOffsetY = -(2 + lift * 3);
  ctx.fill();
  ctx.restore();
  const g = ctx.createRadialGradient(x - r * 0.35, y - r * 0.35, r * 0.1, x, y, r);
  g.addColorStop(0, "rgba(255,255,255,0.55)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
}

function drawNode(n, ctx, scale) {
  const focus = S.hl && S.hl.nodes.has(n.id);
  ctx.save();
  ctx.globalAlpha = S.hl && !focus ? 0.3 : 1;
  const px = 1 / scale;
  const shrink = 1 / Math.max(1, Math.sqrt(scale));
  if (n.kind === "area") {
    const size = ((n.depth === 1 ? 7 : 4.2) + Math.sqrt(n.prs || 0) * 0.3) * shrink;
    const heat = n._heat || 0;
    // a small well drilled into the clay; busier areas are drilled deeper
    const x = n.x - size / 2, y = n.y - size / 2;
    ctx.fillStyle = C.groove;
    ctx.fillRect(x, y, size, size);
    ctx.fillStyle = C.dark + (0.4 + heat * 0.5) + ")";
    ctx.fillRect(x, y, size, Math.max(px, size * 0.22));
    ctx.fillRect(x, y, Math.max(px, size * 0.22), size);
    if (n.owned && S.layers.blueprint) {
      ctx.setLineDash([2.5 * px, 2 * px]);
      ctx.strokeStyle = C.accent;
      ctx.lineWidth = 1.3 * px;
      ctx.strokeRect(x - 3 * px, y - 3 * px, size + 6 * px, size + 6 * px);
      ctx.setLineDash([]);
    }
    const show = (n.depth === 1 && (n.prs >= S.labelMin || scale > 1.3)) || (n.depth === 2 && n.prs >= S.label2Min)
      || focus || scale > 1.8 || S.hoverNode === n;
    if (show) {
      const top = n.depth === 1;
      label(ctx, (top || focus ? n.label : n.name) + "/", n.x + size / 2 + 5 * px, n.y,
        (top ? 13 : 11) * px, top || focus ? 700 : 500, top || focus ? C.fg : C.muted,
        top ? '"Plus Jakarta Sans"' : '"DM Sans"', "left");
    }
  } else if (n.kind === "team") {
    const r = 8;
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
      const ang = (Math.PI / 3) * i + Math.PI / 6;
      ctx.lineTo(n.x + r * Math.cos(ang), n.y + r * Math.sin(ang));
    }
    ctx.closePath();
    ctx.setLineDash([3 * px, 2 * px]);
    ctx.strokeStyle = C.accent;
    ctx.lineWidth = 1.6 * px;
    ctx.stroke();
    ctx.setLineDash([]);
    label(ctx, "@" + n.label, n.x, n.y + r + 11 * px, 11.5 * px, 700, C.accent, '"Plus Jakarta Sans"');
  } else {
    const s = n._s || 0;
    const ghost = s < 0.01;
    const r = (ghost ? 4 : 3.4 + Math.sqrt(s) * 8) * shrink;
    if (S.gone === n.id) {
      // the person who is away: an empty socket where their pebble used to sit
      ctx.beginPath();
      ctx.arc(n.x, n.y, r + 1.5 * px, 0, Math.PI * 2);
      ctx.fillStyle = C.groove;
      ctx.fill();
      ctx.setLineDash([3 * px, 2.5 * px]);
      ctx.strokeStyle = C.risk;
      ctx.lineWidth = 2 * px;
      ctx.stroke();
      ctx.setLineDash([]);
    } else if (ghost) {
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.setLineDash([2 * px, 2 * px]);
      ctx.strokeStyle = n.ghost ? C.accent : C.muted;
      ctx.lineWidth = 1.3 * px;
      ctx.stroke();
      ctx.setLineDash([]);
    } else {
      pebble(ctx, n.x, n.y, r, Math.min(1, s * 1.4));
      if (S.T - (n._last || 0) < 45 * DAY) {
        ctx.fillStyle = C.teal;
        ctx.beginPath(); ctx.arc(n.x, n.y, Math.max(1.3 * px, r * 0.28), 0, Math.PI * 2); ctx.fill();
      }
      if (focus) {
        ctx.strokeStyle = C.teal;
        ctx.lineWidth = 2 * px;
        ctx.beginPath(); ctx.arc(n.x, n.y, r + 3 * px, 0, Math.PI * 2); ctx.stroke();
      }
    }
    if (focus || s > 0.35 || scale > 2.2 || S.hoverNode === n) {
      label(ctx, "@" + n.label + (S.gone === n.id ? " (away)" : ""), n.x, n.y + r + 11 * px, 11.5 * px,
        focus ? 700 : 600, S.gone === n.id ? C.risk : ghost ? C.muted : C.fg, '"Plus Jakarta Sans"');
    }
  }
  ctx.restore();
}

function nodeArea(n, color, ctx) {
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(n.x, n.y, n.kind === "area" ? 7 : 8, 0, 2 * Math.PI);
  ctx.fill();
}

function linkVisible(l) {
  if (linkRisk(l)) return true;
  if (l.kind === "official") return S.layers.blueprint;
  if (l.kind === "collab") return S.layers.collab || linkHl(l);
  if (l.kind === "worn") return S.layers.worn && l._s > 0.002;
  return true;
}

function particles(l) {
  if (l.kind !== "worn" || !S.layers.worn || !l._last) return 0;
  const age = (S.T - l._last) / DAY;
  if (age > 45) return 0;
  const rel = l._s / S.maxScore;
  return rel > 0.4 ? 3 : rel > 0.12 ? 2 : 1;
}

/* ---------- graph setup ---------- */
function initGraph() {
  const el = $("#graph");
  S.fg = ForceGraph()(el)
    .width(el.clientWidth)
    .height(el.clientHeight)
    .backgroundColor("rgba(0,0,0,0)")
    .nodeId("id")
    .nodeCanvasObject(drawNode)
    .nodePointerAreaPaint(nodeArea)
    .linkCanvasObjectMode(() => "replace")
    .linkCanvasObject(drawLink)
    .linkVisibility(linkVisible)
    .linkDirectionalParticles(particles)
    .linkDirectionalParticleWidth(2.6)
    .linkDirectionalParticleSpeed(() => 0.0035)
    .linkDirectionalParticleColor(() => C.teal)
    .cooldownTicks(260)
    .onNodeHover(onHover)
    .onNodeClick(onClick)
    .onBackgroundClick(() => { clearHighlight(); })
    .onEngineStop(() => { if (!S._fitted) { S.fg.zoomToFit(600, 40); S._fitted = true; } });
  S.fg.d3Force("charge").strength((n) => (n.kind === "area" ? -30 : -90)).distanceMax(300);
  S.fg.d3Force("anchor", anchorForce());
  // Areas are pinned, so the default centering force would keep sliding everyone else off-map.
  S.fg.d3Force("center", null);
  S.fg.d3Force("link")
    .distance((l) => ({ tree: 46, official: 110, worn: 85, collab: 140 }[l.kind] || 80))
    .strength((l) => ({ tree: 0.8, official: 0.04, worn: 0.12 + 0.3 * Math.sqrt((l.score || 0) / (S.maxScore || 1)), collab: 0.01 }[l.kind] || 0.1));
  new ResizeObserver(() => S.fg.width(el.clientWidth).height(el.clientHeight)).observe(el);
  el.addEventListener("mousemove", (e) => {
    const h = $("#hover");
    const r = $("#stage").getBoundingClientRect();
    h.style.left = Math.min(e.clientX - r.left + 16, r.width - 310) + "px";
    h.style.top = e.clientY - r.top + 14 + "px";
  });
}

function refresh() {
  if (!S.fg) return;
  S.fg.linkVisibility(linkVisible).linkDirectionalParticles(particles);
}

/* ---------- hover & click ---------- */
function onHover(n) {
  S.hoverNode = n;
  const h = $("#hover");
  $("#graph").style.cursor = n ? "pointer" : "";
  if (!n) { h.style.display = "none"; return; }
  let html = "";
  if (n.kind === "area") {
    const owners = S.data.links.filter((l) => l.kind === "official" && idOf(l.target) === n.id).map((l) => "@" + idOf(l.source).slice(2));
    const walkers = S.data.links.filter((l) => l.kind === "worn" && idOf(l.target) === n.id && l._s > 0.002)
      .sort((a, b) => b._s - a._s).slice(0, 3).map((l) => "@" + idOf(l.source).slice(2));
    html = `<b>${esc(n.label)}/</b><br><span class="k">${n.prs} PRs · ${n.files ?? "?"} files</span><br>
      <span class="k-plan">Officially:</span> ${owners.length ? esc(owners.join(", ")) : '<span class="k-ghost">nobody</span>'}<br>
      <span class="k-walked">Actually walked by:</span> ${esc(walkers.join(", ") || "-")}`;
  } else if (n.kind === "team") {
    html = `<b>@${esc(n.label)}</b><br><span class="k">team in CODEOWNERS</span>`;
  } else {
    const owns = S.data.links.filter((l) => l.kind === "official" && idOf(l.source) === n.id).length;
    html = `<b>@${esc(n.label)}</b><br><span class="k">${n.prs ?? 0} PR touches · last active ${ago(n._last || null, S.T)}</span>
      ${owns ? `<br><span class="k-plan">Official owner of ${owns} area${owns > 1 ? "s" : ""}</span>` : ""}
      ${(n._s || 0) < 0.01 && owns ? '<br><span class="k-ghost">Ghost owner: no recent work here</span>' : ""}`;
  }
  h.innerHTML = html;
  h.style.display = "block";
}

async function onClick(n) {
  if (n.kind === "area") {
    const r = await tool("who_knows", n.label);
    clearHighlight();
    applyHighlight(r.highlight, true);
    addCard(areaCard(r) + proofHtml(r.proof));
  } else if (n.kind === "person") {
    const r = await tool("person", n.label);
    clearHighlight();
    applyHighlight(r.highlight, true);
    addCard(personCard(r) + proofHtml(r.proof));
  }
}

// Buttons inside cards (they are re-rendered as HTML, so delegate from the thread).
document.getElementById("thread").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-gone], button[data-onboard], button[data-n]");
  if (!b || S.busy) return;
  if (b.dataset.gone) goneDirect(b.dataset.gone);
  else if (b.dataset.onboard) onboardDirect(b.dataset.onboard);
  else if (b.dataset.n) routeDirect(+b.dataset.n);
});

async function tool(name, arg, arg2) {
  const [o, n] = S.repo.split("/");
  const qs = new URLSearchParams();
  if (arg != null) qs.set("arg", arg);
  if (arg2 != null) qs.set("arg2", arg2);
  const r = await fetch(`/api/tool/${o}/${n}/${name}?${qs}`);
  return r.json();
}

/* ---------- map layout: areas are fixed districts, people move ---------- */
function layoutMap(data) {
  const areas = data.nodes.filter((n) => n.kind === "area");
  const top = areas.filter((n) => n.depth === 1);
  // Put the busiest districts next to each other, alternating sides so the ring stays balanced.
  top.sort((a, b) => b.prs - a.prs);
  const ring = [];
  top.forEach((n, i) => (i % 2 ? ring.push(n) : ring.unshift(n)));
  const R1 = 150 + top.length * 14;
  const weight = (n) => 1 + Math.sqrt(n.prs || 1) + areas.filter((c) => c.depth === 2 && c.label.startsWith(n.label + "/")).length * 1.5;
  const rawTotal = ring.reduce((s, n) => s + weight(n), 0);
  // Blend proportional and equal spacing so small districts don't pile up.
  const share = (n) => 0.45 * (weight(n) / rawTotal) + 0.55 / ring.length;
  const total = 1;
  let acc = 0;
  for (const n of ring) {
    const w = share(n);
    const ang = ((acc + w / 2) / total) * 2 * Math.PI - Math.PI / 2;
    acc += w;
    n.fx = R1 * Math.cos(ang);
    n.fy = R1 * Math.sin(ang);
    const kids = areas.filter((c) => c.depth === 2 && c.label.startsWith(n.label + "/")).sort((a, b) => b.prs - a.prs);
    const spread = ((w / total) * 2 * Math.PI) * 0.85;
    kids.forEach((c, j) => {
      const t = kids.length === 1 ? 0 : j / (kids.length - 1) - 0.5;
      const a2 = ang + t * spread;
      const r2 = R1 + 90 + (j % 3) * 38;
      c.fx = r2 * Math.cos(a2);
      c.fy = r2 * Math.sin(a2);
    });
  }
  // People start near the middle of the areas they walk.
  const pos = new Map(areas.map((a) => [a.id, a]));
  for (const n of data.nodes) {
    if (n.kind === "area") continue;
    const ls = data.links.filter((l) => idOf(l.source) === n.id && (l.kind === "worn" || l.kind === "official"));
    let x = 0, y = 0, k = 0;
    for (const l of ls) { const a = pos.get(idOf(l.target)); if (a && a.fx != null) { const w = l.score || 0.2; x += a.fx * w; y += a.fy * w; k += w; } }
    n._tx = k ? (x / k) * 0.7 : 0;
    n._ty = k ? (y / k) * 0.7 : 0;
    n.x = n._tx + (Math.random() - 0.5) * 20;
    n.y = n._ty + (Math.random() - 0.5) * 20;
  }
}

// Gently pull every free node toward the centre of the areas it is tied to, so people linked
// only by reviews (or teams linked only by the blueprint) can't drift off the map.
function anchorForce(strength = 0.06) {
  let nodes = [];
  const force = (alpha) => {
    for (const n of nodes) {
      if (n.fx != null || n._tx == null) continue;
      n.vx += (n._tx - n.x) * strength * alpha;
      n.vy += (n._ty - n.y) * strength * alpha;
    }
  };
  force.initialize = (ns) => (nodes = ns);
  return force;
}

/* ---------- highlight ---------- */
function resolveNode(id) {
  const ids = new Set(S.data.nodes.map((n) => n.id));
  if (ids.has(id)) return id;
  if (id.startsWith("a:")) {
    let p = id.slice(2);
    while (p.includes("/")) {
      p = p.slice(0, p.lastIndexOf("/"));
      if (ids.has("a:" + p)) return "a:" + p;
    }
  }
  return null;
}

function applyHighlight(h, replace = false) {
  if (!h) return;
  if (replace || !S.hl) S.hl = { nodes: new Set(), links: new Set(), risk: new Set() };
  for (const id of h.nodes || []) { const r = resolveNode(id); if (r) S.hl.nodes.add(r); }
  const existing = new Set(S.data.links.map((l) => linkKey(idOf(l.source), idOf(l.target))));
  const extra = [];
  for (const [a0, b0, kind] of h.links || []) {
    const a = resolveNode(a0), b = resolveNode(b0);
    if (!a || !b) continue;
    S.hl.nodes.add(a); S.hl.nodes.add(b);
    S.hl.links.add(linkKey(a, b));
    if (kind === "risk") S.hl.risk.add(linkKey(a, b));
    if (!existing.has(linkKey(a, b)) && !existing.has(linkKey(b, a)) && (kind === "path" || kind === "risk")) {
      extra.push({ source: a, target: b, kind });
      existing.add(linkKey(a, b));
    }
  }
  if (extra.length) {
    const d = S.fg.graphData();
    S.fg.graphData({ nodes: d.nodes, links: d.links.concat(extra) });
  }
  if (S.hl.nodes.size) fitTo((n) => S.hl.nodes.has(n.id));
  refresh();
}

// Fit the camera to some nodes, but never zoom in so far that the map loses its context.
function fitTo(filter) {
  S.fg.zoomToFit(700, 110, filter);
  setTimeout(() => { if (S.fg.zoom() > 2.4) S.fg.zoom(2.4, 400); }, 720);
}

function clearHighlight() {
  S.hl = null;
  S.gone = null;
  const d = S.fg.graphData();
  S.fg.graphData({ nodes: d.nodes, links: d.links.filter((l) => l.kind !== "path" && l.kind !== "risk") });
  refresh();
}

/* ---------- timeline ---------- */
function setupTimeline() {
  const ev = S.data.links.filter((l) => l.kind === "worn").flatMap((l) => l.events.map((e) => e[0]));
  S.tMin = Math.min(...ev);
  S.tMax = S.data.now;
  S.T = S.tMax;
  $("#scrub").value = 1000;
  const ticks = [];
  for (let i = 0; i <= 4; i++) ticks.push(`<span>${fmtMonth(S.tMin + ((S.tMax - S.tMin) * i) / 4)}</span>`);
  $("#ticks").innerHTML = ticks.join("");
  $("#when").textContent = "now";
}

function setTime(v) {
  S.T = S.tMin + ((S.tMax - S.tMin) * v) / 1000;
  $("#when").textContent = v >= 999 ? "now" : fmtMonth(S.T);
  recomputeWear();
  refresh();
}

function togglePlay() {
  S.playing = !S.playing;
  $("#play").classList.toggle("playing", S.playing);
  $("#play").setAttribute("aria-label", S.playing ? "Pause time-lapse" : "Play time-lapse");
  if (!S.playing) return;
  let v = +$("#scrub").value;
  if (v >= 999) v = 0;
  const step = () => {
    if (!S.playing) return;
    v = Math.min(1000, v + 4);
    $("#scrub").value = v;
    setTime(v);
    if (v >= 1000) { togglePlay(); return; }
    setTimeout(step, 40);
  };
  step();
}

/* ---------- panel ---------- */
function renderSummary() {
  const r = S.repos.find((x) => x.repo === S.repo);
  const cov = Math.round((r.coverage || 0) * 100);
  const R = 40, len = 2 * Math.PI * R;
  $("#summary").innerHTML = `
    <div class="dial" role="img" aria-label="${cov}% of files have an official owner">
      <div class="dial-ring">
        <svg viewBox="0 0 92 92" width="92" height="92" aria-hidden="true">
          <circle cx="46" cy="46" r="${R}" fill="none" stroke="${C.accent}" stroke-width="5" stroke-linecap="round"
            stroke-dasharray="${(len * cov) / 100} ${len}" />
        </svg>
        <div class="dial-core">${cov}%</div>
      </div>
    </div>
    <div>
      <div class="sum-title">of files have an official owner.<br>The rest is walked, not paved.</div>
      <div class="sum-line">${esc(r.repo)} · ${r.stats.PR} merged PRs · ${r.stats.Person} contributors<br>${fmtMonth(r.from)} to ${fmtMonth(r.to)}</div>
    </div>`;
}

function renderSuggestions() {
  const areas = S.data.nodes.filter((n) => n.kind === "area" && n.depth === 2 && !n.owned).sort((a, b) => b.prs - a.prs);
  const people = S.data.nodes.filter((n) => n.kind === "person").sort((a, b) => (b._s || 0) - (a._s || 0));
  const qs = [
    areas[0] && `Who actually knows ${areas[0].label}?`,
    "Where does CODEOWNERS disagree with reality?",
    people[0] && `What breaks if @${people[0].label} goes on vacation?`,
    "Which open PRs are stuck with the wrong reviewer?",
    "How well does your routing work on past PRs?",
    "What knowledge are we losing?",
    "Who are the hidden bottlenecks in code review?",
    "Draft a CODEOWNERS from how people really work",
  ].filter(Boolean);
  $("#suggest").innerHTML = qs.map((q) => `<button>${esc(q)}</button>`).join("");
  $("#suggest").querySelectorAll("button").forEach((b) => (b.onclick = () => ask(b.textContent)));
}

function addCard(html) {
  if ($("#thread").children.length) $("#suggest").classList.add("compact");
  const div = document.createElement("div");
  div.innerHTML = html;
  $("#thread").appendChild(div);
  $("#thread").scrollTop = 1e9;
}

function evidenceHtml(ev) {
  if (!ev || !ev.length) return "";
  ev = [...new Map(ev.map((e) => [e.url, e])).values()];
  return `<div class="evidence">${ev.slice(0, 8).map((e) =>
    `<a href="${esc(e.url)}" target="_blank" rel="noopener" title="${esc(e.title)}"><b>#${e.number}</b>${esc(e.title)}</a>`).join("")}</div>`;
}

const meter = (share, cls = "") => `<div class="meter ${cls}"><span style="width:${Math.max(3, Math.round(share * 100))}%"></span></div>`;
const initials = (login) => esc(login.replace(/[^A-Za-z0-9]/g, "").slice(0, 2));
const jsonPost = (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => r.json());

function areaCard(r) {
  const d = r.data;
  if (!d.actual_people) {
    const ppl = (d.people || []).map((p) => `<div class="row"><span>@${esc(p.login)}</span><span class="muted">${p.matching_prs} matching PRs · ${esc(p.last)}</span></div>`).join("");
    return `<div class="card"><h3>${esc(d.topic || "Topic")}</h3>${ppl || '<div class="muted">No matching work found.</div>'}${evidenceHtml(r.evidence)}</div>`;
  }
  const off = Array.isArray(d.official_owners)
    ? d.official_owners.map((o) => `<div class="row"><span class="plan">@${esc(o.owner)}</span><span class="muted">${o.active_here ? "active " + esc(o.active_here.last) : o.kind === "Team" ? "team" : "no recent work here"}</span></div>`).join("")
    : '<div class="row muted">Nobody. No CODEOWNERS rule covers it.</div>';
  const ppl = d.actual_people.map((p) => `<div class="row"><span>@${esc(p.login)}</span><span class="muted">${p.authored} authored · ${p.reviewed} reviewed · ${esc(p.last_active)}</span></div>${meter(p.share_of_recent_work)}`).join("");
  return `<div class="card"><h3>${esc(d.area)}/</h3>
    <div class="caps">Officially</div>${off}
    <div class="caps">Actually · share of recent work</div>${ppl}
    ${evidenceHtml(r.evidence)}
    <div class="card-actions"><button class="btn btn-sm" data-onboard="${esc(d.area)}">How do I start contributing here?</button></div></div>`;
}

function personCard(r) {
  const d = r.data;
  const works = d.works_on.map((w) => `<div class="row"><span>${esc(w.area)}/</span><span class="muted">${w.prs} PRs · ${esc(w.last)}</span></div>`).join("");
  const rev = d.reviews_with.map((w) => `@${esc(w.login)} (${w.reviews})`).join(", ");
  const owns = Array.isArray(d.officially_owns) ? d.officially_owns.map(esc).join(", ") : esc(d.officially_owns);
  return `<div class="card"><h3>@${esc(d.login)}</h3>
    <div class="caps">Actually works on</div>${works}
    <div class="caps">Reviews with</div><div>${rev || "-"}</div>
    <div class="caps">Officially owns</div><div class="plan">${owns}</div>
    <div class="card-actions"><button class="btn btn-sm" data-gone="${esc(d.login)}">What breaks if @${esc(d.login)} is away?</button></div></div>`;
}

/* ---------- decisions & actions ---------- */
function decisionCard(d) {
  if (typeof d === "string") return `<div class="card muted">${esc(d)}</div>`;
  const pr = d.pr;
  const dec = d.decision || {};
  const touches = (d.touches || []).map((t) => `<div class="touch"><span>${esc(t.area)}/</span>${meter(t.share_of_change)}</div>`).join("");
  const hop = (c, role, why) => c ? `<div class="hop">
      <div class="well"><span>${initials(c.login)}</span></div>
      <div><div class="role">${role}</div><div class="who">@${esc(c.login)}</div>
        <div class="why">${Math.round(c.share_of_recent_work_there * 100)}% of recent work in <code>${esc(c.main_area)}/</code> · ${c.prs_there} PRs · active ${esc(c.last_active)} · ${c.open_review_requests} open reviews${c.has_reviewed_author_before ? ` · reviewed @${esc(pr.author)} ${c.has_reviewed_author_before}× before` : ""}${why ? `<br><i>${esc(why)}</i>` : ""}</div></div>
    </div>` : "";
  const ruled = (d.considered_and_rejected || []).map((r) => `<s>@${esc(r.login)}</s> ${esc(r.why_not)}`).join(" · ");
  const verdicts = Array.isArray(d.currently_requested)
    ? d.currently_requested.map((v) => `<div class="verdict">Requested @${esc(v.requested)}: <span class="${v.verdict === "good choice" ? "good" : "bad"}">${esc(v.verdict)}</span></div>`).join("")
    : `<div class="verdict"><span class="bad">${esc(d.currently_requested || "")}</span></div>`;
  const past = (d.past_actions_on_this_pr || []).map((a) => `${esc(a.kind)} ${esc(a.status)} ${esc(a.when)}`).join(", ");
  const act = d.proposed_action;
  const id = "act-" + Math.random().toString(36).slice(2);
  if (act) setTimeout(() => wireApprove(id, act), 0);
  return `<div class="decision">
    <div class="pr-title"><a href="${esc(pr.url)}" target="_blank" rel="noopener">#${pr.number} ${esc(pr.title)}</a></div>
    <div class="pr-meta">by @${esc(pr.author)} · opened ${esc(pr.opened)}${pr.draft ? " · draft" : ""}</div>
    <div class="caps">Touches</div>${touches}
    <div class="chain">${hop(dec.primary, "Primary reviewer", "")}${hop(dec.second, "Second reviewer", dec.why_second)}</div>
    ${ruled ? `<div class="ruled">Ruled out: ${ruled}</div>` : ""}
    ${verdicts}
    ${past ? `<div class="ruled">Already done: ${past}</div>` : ""}
    ${act ? `<div class="approve" id="${id}"><button class="btn btn-primary">Request ${act.reviewers.map((r) => "@" + esc(r)).join(" + ")}</button><span class="note">Checking permissions…</span></div>` : ""}
  </div>`;
}

async function wireApprove(id, act) {
  const box = document.getElementById(id);
  if (!box) return;
  const btn = box.querySelector("button"), note = box.querySelector(".note");
  const body = { repo: S.repo, kind: "request_reviewers", number: act.number, reviewers: act.reviewers };
  const plan = await jsonPost("/api/act", body);
  note.textContent = plan.can_write ? "You have write access. This requests the reviews on GitHub."
    : !plan.writes_enabled ? "Demo server: approving records a dry run in the graph. Nothing is posted."
    : `Read-only on ${S.repo}: approving records a dry run. Nothing is posted.`;
  btn.onclick = async () => {
    btn.disabled = true;
    note.textContent = "Working…";
    const r = await jsonPost("/api/act", { ...body, confirm: true });
    note.innerHTML = r.status === "done" ? `Done. <a href="${esc(r.url)}" target="_blank" rel="noopener">View on GitHub</a>`
      : r.status === "dry-run" ? `Dry run recorded in the graph: ${esc(r.reason)}.` : `Failed: ${esc(r.error || r.status)}`;
  };
}

function queueCard(d) {
  const rows = d.queue.map((q) => `<div class="qrow sev${q.severity}">
      <div class="t"><div class="title" title="${esc(q.title)}"><a href="${esc(q.url)}" target="_blank" rel="noopener">#${q.number}</a>${esc(q.title)}</div>
        <div class="prob">${esc(q.problems[0])}</div>
        <div class="sug">${q.suggested.length ? "Suggest " + q.suggested.map((x) => "@" + esc(x)).join(", ") : ""}</div></div>
      <button class="btn btn-sm" data-n="${q.number}">Route</button></div>`).join("");
  const kinds = { unrouted: "nobody asked", "absent-reviewer": "absent reviewer", "wrong-reviewer": "wrong reviewer", waiting: "waiting 14d+" };
  return `<div class="decision"><div class="pr-title">Review queue triage</div>
    <div class="stats3"><div><b>${d.open_prs_checked}</b>open PRs checked</div><div><b class="risk">${d.flagged}</b>misrouted</div>
      ${Object.entries(d.by_problem).map(([k, v]) => `<div><b>${v}</b>${esc(kinds[k] || k)}</div>`).join("")}</div>
    <div class="queue">${rows}</div></div>`;
}

async function routeDirect(n) {
  addCard(`<div class="msg-user">Route PR #${n}</div>`);
  const r = await tool("route_pr", String(n));
  clearHighlight();
  applyHighlight(r.highlight, true);
  addCard(decisionCard(r.data) + evidenceHtml(r.evidence) + proofHtml(r.proof));
}

async function triageDirect() {
  addCard(`<div class="msg-user">Triage the review queue</div>`);
  const r = await tool("triage");
  clearHighlight();
  applyHighlight(r.highlight, true);
  addCard(queueCard(r.data) + proofHtml(r.proof));
}

async function goneDirect(login) {
  addCard(`<div class="msg-user">What breaks if @${esc(login)} is away?</div>`);
  const r = await tool("if_gone", login);
  showGone(r);
  addCard(goneCard(r.data) + evidenceHtml(r.evidence) + proofHtml(r.proof));
}

function showGone(r) {
  clearHighlight();
  if (r.data && r.data.person) S.gone = "p:" + r.data.person;
  applyHighlight(r.highlight, true);
}

async function onboardDirect(area) {
  addCard(`<div class="msg-user">How do I start contributing to ${esc(area)}/?</div>`);
  const r = await tool("onboard", area);
  clearHighlight();
  applyHighlight(r.highlight, true);
  addCard(onboardCard(r.data) + proofHtml(r.proof));
}

async function compareDirect() {
  addCard(`<div class="msg-user">Compare routing strategies</div>`);
  const [cmp, bt] = await Promise.all([tool("compare_routing"), tool("backtest")]);
  clearHighlight();
  applyHighlight(cmp.highlight, true);
  addCard(compareCard(cmp.data, bt.data) + evidenceHtml(bt.evidence) + proofHtml([...(cmp.proof || []), ...(bt.proof || [])]));
}

/* ---------- what-ifs, strategies, onboarding ---------- */
const at = (x) => (x ? "@" + esc(x) : '<span class="muted">nobody</span>');
const pct = (x) => Math.round((x || 0) * 100) + "%";

function goneCard(d) {
  if (typeof d === "string") return `<div class="card muted">${esc(d)}</div>`;
  const s = d.summary;
  const chip = (st) => `<span class="chip ${st}">${st}</span>`;
  const areas = d.areas.map((a) => `<div class="arow">
      <span class="mono">${esc(a.area)}/</span>${chip(a.status)}
      <span class="muted">they do ${pct(a.their_share)} · next: ${at(a.next_person)}${a.next_share ? " (" + pct(a.next_share) + ")" : ""}</span></div>`).join("");
  const backup = { none: "no backup", weak: "weak backup", ok: "backup ok" };
  const prs = d.stuck_prs.map((p) => `<div class="qrow ${p.backup === "ok" ? "sev1" : "sev3"}">
      <div class="t"><div class="title" title="${esc(p.title)}"><a href="${esc(p.url)}" target="_blank" rel="noopener">#${p.number}</a>${esc(p.title)}</div>
        <div class="prob">${esc(p.why)} · ${backup[p.backup]}</div>
        <div class="sug">Next: ${at(p.next_reviewer)}${p.next_fit_vs_theirs != null ? ` · ${pct(p.next_fit_vs_theirs)} of their fit` : ""}</div></div>
      <button class="btn btn-sm" data-n="${p.number}">Route</button></div>`).join("");
  const authors = d.authors_losing_their_main_reviewer.map((a) => `@${esc(a.author)} (${pct(a.share_of_their_reviews)} of their reviews)`).join(", ");
  return `<div class="decision"><div class="pr-title">If @${esc(d.person)} is away</div>
    <div class="pr-meta">Removed from the graph, then every path re-walked without them.</div>
    <div class="stats3"><div><b class="risk">${s.open_prs_stuck}</b>open PRs lose their reviewer</div>
      <div><b class="risk">${s.stuck_prs_without_a_good_backup}</b>with no good backup</div>
      <div><b>${s.areas_orphaned + s.areas_thin}</b>areas orphaned or thin</div></div>
    ${areas ? `<div class="caps">Where they carry the work</div>${areas}` : ""}
    ${authors ? `<div class="caps">Loses their main reviewer</div><div class="muted">${authors}</div>` : ""}
    ${prs ? `<div class="caps">Stuck PRs</div><div class="queue">${prs}</div>` : ""}</div>`;
}

function compareCard(c, b) {
  const by = (rows, key) => rows.find((r) => r.key === key) || {};
  const q = { codeowners: by(c.strategies, "codeowners"), graph: by(c.strategies, "graph"), balanced: by(c.strategies, "balanced") };
  const h = b ? { codeowners: by(b.strategies, "codeowners"), graph: by(b.strategies, "graph"), balanced: by(b.strategies, "balanced") } : null;
  const cols = ["codeowners", "graph", "balanced"];
  const row = (label, f, best) => `<tr><th>${label}</th>${cols.map((k) => `<td class="${k === best ? "win" : ""}">${f(k)}</td>`).join("")}</tr>`;
  const n = c.open_prs;
  const busiest = (k) => q[k].most_on_one_person ? `${q[k].most_on_one_person}` : "-";
  const mf = c.max_flow;
  return `<div class="decision"><div class="pr-title">Three ways to route the queue</div>
    <div class="pr-meta">${n} open PRs today${b ? ` · ${b.prs_tested} merged PRs replayed` : ""}</div>
    <table class="cmp">
      <thead><tr><th></th><th>CODE&shy;OWNERS</th><th>Graph</th><th>Balanced</th></tr></thead>
      <tbody>
        <tr class="sec"><td colspan="4">Open queue now</td></tr>
        ${row("Named reviewer", (k) => `${q[k].prs_with_an_accountable_reviewer}/${n}`, "graph")}
        ${row("Knows the code", (k) => `${q[k].reviewer_has_recent_work_there}/${n}`, "graph")}
        ${row("Most on one person", busiest, "balanced")}
        ${row("People sharing it", (k) => q[k].people_used, "balanced")}
        ${h ? `<tr class="sec"><td colspan="4">Replay: picked a real reviewer?</td></tr>
        ${row("Top pick", (k) => pct(h[k].hit_at_1), null)}
        ${row("Either of top 2", (k) => pct(h[k].hit_at_2), "graph")}` : ""}
      </tbody>
    </table>
    ${h ? `<div class="ruled">Baseline "the two busiest reviewers" gets ${pct(by(b.strategies, "busiest").hit_at_2)} on the same replay. Only activity from before each PR is used.</div>` : ""}
    <div class="ruled">Balanced = <code>algo.maxFlow</code> on source → PR → qualified reviewer → sink. The smallest per-person cap that still covers every PR is <b>${mf.per_person_cap}</b>, solved in ${mf.solve_ms} ms.
      Load: ${Object.entries(c.balanced_load).map(([k, v]) => `@${esc(k)} ${v}`).join(", ")}.</div></div>`;
}

function backtestCard(b) {
  const rows = b.strategies.map((r) => `<tr><th>${esc(r.strategy)}</th><td>${pct(r.hit_at_1)}</td><td class="${r.key === "graph" ? "win" : ""}">${pct(r.hit_at_2)}</td><td>${r.people_used}</td></tr>`).join("");
  return `<div class="decision"><div class="pr-title">Backtest: ${b.prs_tested} merged PRs</div>
    <div class="pr-meta">${esc(b.method)}</div>
    <table class="cmp"><thead><tr><th></th><th>Top pick</th><th>Top 2</th><th>People</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function fadingCard(rows) {
  if (!Array.isArray(rows) || !rows.length) return `<div class="card muted">No fading knowledge found.</div>`;
  return `<div class="decision"><div class="pr-title">Knowledge that is fading</div>
    <div class="pr-meta">Areas whose main expert has not worked there in 3+ months.</div>
    ${rows.map((o) => `<div class="arow col">
      <div><span class="mono">${esc(o.area)}/${o.subareas ? `{${o.subareas.slice(0, 4).map(esc).join(", ")}${o.subareas.length > 4 ? ", +" + (o.subareas.length - 4) : ""}}` : ""}</span>
        <span class="chip ${o.picked_up_by ? "thin" : "orphaned"}">${o.picked_up_by ? "handover" : "at risk"}</span></div>
      <div class="muted">@${esc(o.expert)} did ${pct(o.expert_share_of_all_prs)} of ${o.prs} PRs, last here ${esc(o.expert_last_here)}. ${o.picked_up_by ? `Picked up by @${esc(o.picked_up_by)}.` : esc(o.risk) + "."}</div></div>`).join("")}</div>`;
}

function onboardCard(d) {
  if (typeof d === "string") return `<div class="card muted">${esc(d)}</div>`;
  const ask = Array.isArray(d.ask) ? d.ask.map((g) => `<div class="row"><span>@${esc(g.login)}</span><span class="muted">${g.reviews_here} reviews here · ${pct(g.share_of_recent_work)} of recent work · ${esc(g.last_active)}</span></div>`).join("") : `<div class="muted">${esc(d.ask)}</div>`;
  const read = d.read_first.map((r) => `<a href="${esc(r.url)}" target="_blank" rel="noopener" title="${esc(r.title)}"><b>#${r.number}</b>${esc(r.title)}</a>`).join("");
  const near = d.usually_changes_with.map((x) => `<code>${esc(x.area)}/</code>`).join(" ");
  const chain = d.intro_chain && d.intro_chain.chain ? `<div class="caps">Your intro chain</div><div>${d.intro_chain.chain.map((x) => "@" + esc(x)).join(" → ")}</div>` : "";
  return `<div class="card"><h3>Start contributing to ${esc(d.area)}/</h3>
    <div class="caps">Ask</div>${ask}
    <div class="caps">Read these first (small, well discussed)</div><div class="evidence">${read}</div>
    ${near ? `<div class="caps">Usually changes with</div><div>${near}</div>` : ""}${chain}</div>`;
}

function proofHtml(proof) {
  if (!proof || !proof.length) return "";
  const ms = proof.reduce((t, p) => t + (p.ms || 0), 0);
  const runs = proof.reduce((t, p) => t + (p.runs || 1), 0);
  const params = (p) => Object.keys(p.params || {}).length ? `<div class="pp">${esc(JSON.stringify(p.params))}</div>` : "";
  return `<details class="proof"><summary>Graph proof · ${runs} Cypher ${runs === 1 ? "query" : "queries"} on FalkorDB${ms ? ` · ${Math.round(ms)} ms` : ""}</summary>
    ${proof.map((p) => `<div class="pq"><div class="pm">${p.runs > 1 ? `ran ${p.runs}× · ` : ""}${p.rows} rows${p.ms != null ? ` · ${p.ms} ms` : ""}</div><pre>${esc(p.cypher)}</pre>${params(p)}</div>`).join("")}</details>`;
}

/* ---------- asking ---------- */
async function ask(q) {
  q = (q || $("#ask").value).trim();
  if (!q || S.busy) return;
  S.busy = true;
  $("#ask-btn").disabled = true;
  $("#ask").value = "";
  clearHighlight();
  addCard(`<div class="msg-user">${esc(q)}</div>`);
  $("#suggest").classList.add("compact");
  const box = document.createElement("div");
  box.className = "msg-agent";
  box.innerHTML = `<div class="walk"><div class="pending"><span class="foot"></span><span>reading the graph…</span></div></div><div class="answer"></div><div class="cards"></div>`;
  $("#thread").appendChild(box);
  const walk = box.querySelector(".walk");
  const steps = [];
  let answer = "";
  try {
    const res = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo: S.repo, question: q, history: S.history }),
    });
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!chunk.startsWith("data: ")) continue;
        const ev = JSON.parse(chunk.slice(6));
        if (ev.type === "model") $("#model").textContent = `Agent: ${ev.provider} · ${ev.model} · Graph: FalkorDB`;
        if (ev.type === "step") {
          steps.push(ev);
          walk.innerHTML = steps.map((s, k) => `<div data-k="${k}"><span class="foot"></span><span>${esc(s.label)}</span></div>`).join("")
            + `<div class="pending"><span class="foot"></span><span>walking…</span></div>`;
          walk.querySelectorAll("[data-k]").forEach((d) => (d.onclick = () => applyHighlight(steps[+d.dataset.k].highlight, true)));
          applyHighlight(ev.highlight);
          if (ev.tool === "route_pr" && ev.data && ev.data.pr) box.querySelector(".cards").insertAdjacentHTML("beforeend", decisionCard(ev.data));
          if (ev.tool === "triage" && ev.data && ev.data.queue) box.querySelector(".cards").insertAdjacentHTML("beforeend", queueCard(ev.data));
          if (ev.tool === "pave") box.querySelector(".cards").insertAdjacentHTML("beforeend", `<div class="approve"><button class="btn btn-primary" onclick="pave()">Review the CODEOWNERS draft</button></div>`);
          const card = ev.data && { if_gone: goneCard, compare_routing: (d) => compareCard(d, null), backtest: backtestCard, fading_paths: fadingCard, onboard: onboardCard }[ev.tool];
          if (card) box.querySelector(".cards").insertAdjacentHTML("beforeend", card(ev.data));
          if (ev.tool === "if_gone" && ev.data && ev.data.person) S.gone = "p:" + ev.data.person;
          $("#thread").scrollTop = 1e9;
        }
        if (ev.type === "answer") {
          answer = ev.text;
          walk.querySelector(".pending")?.remove();
          if (!steps.length) walk.remove();
          box.querySelector(".answer").innerHTML = marked.parse(ev.text || "") + evidenceHtml(ev.evidence)
            + proofHtml(steps.flatMap((s) => s.proof || []));
          box.querySelectorAll(".answer a").forEach((a) => { a.target = "_blank"; a.rel = "noopener"; });
          const gone = steps.find((s) => s.tool === "if_gone");
          applyHighlight(gone ? gone.highlight : ev.highlight, true);
          if (steps.some((s) => s.tool === "pave")) markPaved(steps.find((s) => s.tool === "pave").highlight);
        }
        if (ev.type === "error") {
          walk.querySelector(".pending")?.remove();
          box.querySelector(".answer").innerHTML = `<div class="error">${esc(ev.text)}</div>`;
        }
      }
      $("#thread").scrollTop = 1e9;
    }
    S.history.push({ role: "user", content: q }, { role: "assistant", content: answer });
  } catch (e) {
    box.querySelector(".answer").innerHTML = `<div class="error">${esc(e.message)}</div>`;
  } finally {
    S.busy = false;
    $("#ask-btn").disabled = false;
  }
}

/* ---------- paving ---------- */
function markPaved(h) {
  S.paved = new Set();
  for (const [a0, b0] of h.links || []) {
    const a = resolveNode(a0), b = resolveNode(b0);
    if (a && b) S.paved.add(linkKey(a, b));
  }
  refresh();
}

async function pave() {
  const r = await tool("pave");
  const d = r.data;
  $("#pave-changes").innerHTML = d.changes_vs_today.map((c) => {
    const today = Array.isArray(c.today) ? c.today : [];
    return `<div class="chg"><span class="area">${esc(c.area)}/</span><span class="from ${today.length ? "" : "none"}">${today.length ? today.map((x) => "@" + esc(x)).join(" ") : "nobody"}</span><span>→</span><span class="to">${c.suggested.map((x) => "@" + esc(x)).join(" ")}</span></div>`;
  }).join("");
  $("#pave-code").textContent = "# Paved by Desire Paths from who actually authored and reviewed recent work.\n"
    + "# These lines cover code that no existing rule owns today.\n\n" + d.codeowners
    + "\n\n# ---- Your existing CODEOWNERS rules follow, unchanged. They come last, so they still win. ----";
  $("#pave-status").textContent = "";
  $("#pave-dialog").showModal();
  markPaved(r.highlight);
  const plan = await jsonPost("/api/act", { repo: S.repo, kind: "open_codeowners_pr" });
  const btn = $("#pave-pr-btn");
  btn.disabled = false;
  $("#pave-status").textContent = plan.target ? `Opens a PR on ${plan.target}.`
    : !plan.writes_enabled ? "Demo server: approving records a dry run." : `Read-only on ${S.repo} and no fork: approving records a dry run.`;
  btn.onclick = async () => {
    btn.disabled = true;
    $("#pave-status").textContent = "Opening PR…";
    const res = await jsonPost("/api/act", { repo: S.repo, kind: "open_codeowners_pr", confirm: true });
    $("#pave-status").innerHTML = res.status === "done" ? `Opened: <a href="${esc(res.url)}" target="_blank" rel="noopener">${esc(res.url)}</a>` : esc(res.reason || res.error || res.status);
  };
}

/* ---------- repos & ingest ---------- */
async function loadRepos(select) {
  S.repos = await (await fetch("/api/repos")).json();
  $("#repo").innerHTML = S.repos.map((r) => `<option value="${esc(r.repo)}">${esc(r.repo)}</option>`).join("");
  const pick = select || new URLSearchParams(location.search).get("repo") || (S.repos.find((r) => r.repo === "FalkorDB/FalkorDB") || S.repos[0])?.repo;
  if (pick) { $("#repo").value = pick; await loadRepo(pick); }
}

async function loadRepo(repo) {
  S.repo = repo;
  S.hl = null; S.paved = new Set(); S.history = []; S._fitted = false;
  history.replaceState(null, "", `?repo=${encodeURIComponent(repo)}`);
  const [o, n] = repo.split("/");
  S.data = await (await fetch(`/api/graph/${o}/${n}`)).json();
  setupTimeline();
  recomputeWear();
  if (!S.fg) initGraph();
  layoutMap(S.data);
  const planLinks = S.data.links.filter((l) => l.kind === "official").length;
  S.planFade = Math.min(1, 60 / Math.max(planLinks, 1)) ** 0.5;
  const tops = S.data.nodes.filter((n) => n.kind === "area" && n.depth === 1).map((n) => n.prs).sort((a, b) => b - a);
  S.labelMin = tops[Math.min(13, tops.length - 1)] || 0;
  const subs = S.data.nodes.filter((n) => n.kind === "area" && n.depth === 2).map((n) => n.prs).sort((a, b) => b - a);
  S.label2Min = Math.max(3, subs[Math.min(9, subs.length - 1)] || 0);
  setTimeout(() => S.fg.zoomToFit(600, 40), 1800);
  S.fg.graphData({ nodes: S.data.nodes, links: S.data.links });
  renderSummary();
  renderSuggestions();
  $("#thread").innerHTML = "";
  $("#suggest").classList.remove("compact");
  addCard(`<div class="intro">Every <b class="k-walked">groove</b> is worn by real pull requests and reviews, and grows shallow when people stop walking it.
    The <span class="k-plan">dashed violet</span> lines are what CODEOWNERS says. Press play to watch ${esc(repo)} wear in, click any area or person, or ask below.</div>`);
}

async function ingest(e) {
  e.preventDefault();
  const repo = $("#ingest-input").value.trim().replace(/^https?:\/\/github\.com\//, "").replace(/\/$/, "");
  if (!repo.includes("/")) return;
  addCard(`<div class="msg-agent walk"><div class="pending" id="ing"><span class="foot"></span><span>walking ${esc(repo)}: starting…</span></div></div>`);
  await fetch("/api/ingest", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ repo }) });
  const [o, n] = repo.split("/");
  for (;;) {
    await new Promise((r) => setTimeout(r, 2000));
    const st = await (await fetch(`/api/ingest/${o}/${n}`)).json();
    const el = $("#ing");
    if (st.state === "running") { if (el) el.lastElementChild.textContent = `walking ${repo}: ${st.step}…`; continue; }
    if (el) el.id = "";
    if (st.state === "done") { await loadRepos(repo); return; }
    addCard(`<div class="error">Could not load ${esc(repo)}: ${esc(st.error || st.state)}</div>`);
    return;
  }
}

/* ---------- wire up ---------- */
$("#repo").onchange = (e) => loadRepo(e.target.value);
$("#ingest-form").onsubmit = ingest;
$("#ask-form").onsubmit = (e) => { e.preventDefault(); ask(); };
$("#ask").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } });
$("#scrub").oninput = (e) => setTime(+e.target.value);
$("#play").onclick = togglePlay;
$("#pave-btn").onclick = pave;
$("#queue-btn").onclick = triageDirect;
$("#compare-btn").onclick = compareDirect;
$("#copy-btn").onclick = () => navigator.clipboard.writeText($("#pave-code").textContent);
document.querySelectorAll("#layers [data-layer]").forEach((b) => (b.onclick = () => {
  S.layers[b.dataset.layer] = !S.layers[b.dataset.layer];
  b.classList.toggle("on", S.layers[b.dataset.layer]);
  b.setAttribute("aria-pressed", String(S.layers[b.dataset.layer]));
  refresh();
}));
fetch("/api/health").then((r) => r.json()).then((h) => ($("#model").textContent = `Agent: ${h.llm} · Graph: FalkorDB`));
loadRepos();
