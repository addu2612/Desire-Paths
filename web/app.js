/* Desire Paths UI */
const DAY = 86400;
const TAU = (120 * DAY) / Math.LN2; // same half-life as the backend
const C = {
  blue: "#5fb3ff", amber: "#ffb547", hot: "#ffd28a", ghost: "#9ad1ff", ink: "#e9eef4",
  ink2: "#9fb0c3", ink3: "#5f7387", risk: "#ff6b5e", white: "#ffffff",
};

const S = {
  repo: null, repos: [], data: null, fg: null,
  T: null, tMin: 0, tMax: 0,
  layers: { blueprint: true, worn: true, collab: false },
  hl: null, // {nodes:Set, links:Set}
  paved: new Set(),
  history: [], busy: false, playing: false,
  maxScore: 1, hoverNode: null,
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
  let max = 0.001;
  for (const l of S.data.links) {
    if (l.kind !== "worn") continue;
    const { s, last } = wearAt(l, T);
    l._s = s; l._last = last;
    max = Math.max(max, s);
    const p = idOf(l.source), a = idOf(l.target);
    personScore.set(p, (personScore.get(p) || 0) + s);
    areaHeat.set(a, (areaHeat.get(a) || 0) + s);
  }
  S.maxScore = max;
  let maxP = 0.001, maxA = 0.001;
  personScore.forEach((v) => (maxP = Math.max(maxP, v)));
  areaHeat.forEach((v) => (maxA = Math.max(maxA, v)));
  for (const n of S.data.nodes) {
    if (n.kind === "person") n._s = (personScore.get(n.id) || 0) / maxP;
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

function drawLink(l, ctx, scale) {
  const a = l.source, b = l.target;
  if (a.x == null || b.x == null) return;
  const hl = linkHl(l);
  const dim = S.hl && !hl ? 0.08 : 1;
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(a.x, a.y);
  ctx.lineTo(b.x, b.y);
  if (l.kind === "tree") {
    ctx.strokeStyle = `rgba(233,238,244,${0.07 * dim})`;
    ctx.lineWidth = 0.6 / scale;
    ctx.stroke();
  } else if (l.kind === "official") {
    ctx.setLineDash([5 / scale, 4 / scale]);
    ctx.strokeStyle = hl ? C.blue : `rgba(95,179,255,${(l.coverage >= 0.5 ? 0.42 : 0.22) * dim})`;
    ctx.lineWidth = (hl ? 2.2 : 0.9) / scale;
    ctx.stroke();
  } else if (l.kind === "collab") {
    ctx.strokeStyle = `rgba(233,238,244,${(hl ? 0.8 : 0.16) * dim})`;
    ctx.lineWidth = (hl ? 2 : 0.4 + Math.min(l.n, 30) / 25) / scale;
    ctx.stroke();
  } else if (l.kind === "worn") {
    const rel = Math.sqrt(l._s / S.maxScore);
    const age = l._last ? (S.T - l._last) / DAY : 999;
    const fresh = Math.max(0.12, Math.exp(-age / 100));
    const paved = S.paved.has(linkKey(idOf(a), idOf(b)));
    if (paved) {
      ctx.strokeStyle = C.blue;
      ctx.lineWidth = (2.6 + rel * 3) / scale;
      ctx.globalAlpha = 0.9 * dim;
      ctx.stroke();
      ctx.strokeStyle = C.white;
      ctx.lineWidth = 0.8 / scale;
      ctx.stroke();
    } else {
      ctx.lineCap = "round";
      ctx.shadowColor = C.amber;
      const strong = rel * fresh;
      ctx.shadowBlur = hl ? 18 : strong > 0.25 ? 12 * strong : 0;
      ctx.strokeStyle = hl ? C.hot : C.amber;
      ctx.globalAlpha = (hl ? 1 : 0.08 + 0.92 * Math.pow(strong, 0.8)) * dim;
      ctx.lineWidth = ((hl ? 1.5 : 0) + 0.4 + rel * 5.5) / scale;
      ctx.stroke();
    }
  } else if (l.kind === "path") {
    ctx.strokeStyle = C.white;
    ctx.lineWidth = 2.5 / scale;
    ctx.shadowColor = C.white;
    ctx.shadowBlur = 14;
    ctx.stroke();
  }
  ctx.restore();
}

function drawNode(n, ctx, scale) {
  const hl = isHl(n.id);
  const focus = S.hl && S.hl.nodes.has(n.id);
  const alpha = S.hl && !hl ? 0.15 : 1;
  ctx.save();
  ctx.globalAlpha = alpha;
  const px = 1 / scale;
  if (n.kind === "area") {
    const size = ((n.depth === 1 ? 4.5 : 2.6) + Math.sqrt(n.prs || 0) * 0.25) / Math.max(1, Math.sqrt(scale));
    const heat = n._heat || 0;
    ctx.fillStyle = `rgba(233,238,244,${0.25 + heat * 0.75})`;
    ctx.fillRect(n.x - size / 2, n.y - size / 2, size, size);
    if (n.owned && S.layers.blueprint) {
      ctx.strokeStyle = C.blue;
      ctx.lineWidth = 1.2 * px;
      ctx.strokeRect(n.x - size / 2 - 2 * px, n.y - size / 2 - 2 * px, size + 4 * px, size + 4 * px);
    }
    const showLabel = (n.depth === 1 && (n.prs >= S.labelMin || scale > 1.3)) || (n.depth === 2 && n.prs >= S.label2Min)
      || focus || scale > 1.8 || S.hoverNode === n;
    if (showLabel) {
      const fs = (n.depth === 1 ? 12 : 10.5) * px;
      ctx.font = `${n.depth === 1 ? 600 : 400} ${fs}px "JetBrains Mono"`;
      ctx.fillStyle = focus ? C.white : n.depth === 1 ? C.ink : C.ink2;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText((n.depth === 1 || focus ? n.label : n.name) + "/", n.x + size / 2 + 4 * px, n.y);
    }
  } else if (n.kind === "team") {
    const r = 6;
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
      const ang = (Math.PI / 3) * i + Math.PI / 6;
      ctx.lineTo(n.x + r * Math.cos(ang), n.y + r * Math.sin(ang));
    }
    ctx.closePath();
    ctx.strokeStyle = C.blue;
    ctx.setLineDash([3 * px, 2 * px]);
    ctx.lineWidth = 1.5 * px;
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.font = `500 ${11 * px}px "JetBrains Mono"`;
    ctx.fillStyle = C.blue;
    ctx.textAlign = "center";
    ctx.fillText("@" + n.label, n.x, n.y + r + 10 * px);
  } else {
    const s = n._s || 0;
    const ghost = s < 0.01;
    const r = (ghost ? 3.2 : 2.4 + Math.sqrt(s) * 7) / Math.max(1, Math.sqrt(scale));
    ctx.beginPath();
    ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
    if (ghost) {
      ctx.setLineDash([2 * px, 2 * px]);
      ctx.strokeStyle = n.ghost ? C.ghost : C.ink3;
      ctx.lineWidth = 1.2 * px;
      ctx.stroke();
      ctx.setLineDash([]);
    } else {
      ctx.shadowColor = C.amber;
      ctx.shadowBlur = focus ? 22 : 8;
      ctx.fillStyle = focus ? C.hot : C.amber;
      ctx.fill();
    }
    const showLabel = focus || s > 0.35 || scale > 2.2 || S.hoverNode === n;
    if (showLabel) {
      ctx.shadowBlur = 0;
      ctx.font = `${focus ? 600 : 400} ${10.5 * px}px "JetBrains Mono"`;
      ctx.fillStyle = ghost ? (n.ghost ? C.ghost : C.ink3) : focus ? C.white : C.hot;
      ctx.textAlign = "center";
      ctx.fillText("@" + n.label, n.x, n.y + r + 10 * px);
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
    .linkDirectionalParticleWidth(2.2)
    .linkDirectionalParticleSpeed(() => 0.0035)
    .linkDirectionalParticleColor(() => C.hot)
    .cooldownTicks(260)
    .onNodeHover(onHover)
    .onNodeClick(onClick)
    .onBackgroundClick(() => { clearHighlight(); })
    .onEngineStop(() => { if (!S._fitted) { S.fg.zoomToFit(600, 90); S._fitted = true; } });
  S.fg.d3Force("charge").strength((n) => (n.kind === "area" ? -30 : -90)).distanceMax(300);
  S.fg.d3Force("link")
    .distance((l) => ({ tree: 46, official: 110, worn: 85, collab: 140 }[l.kind] || 80))
    .strength((l) => ({ tree: 0.8, official: 0.04, worn: 0.12 + 0.3 * Math.sqrt((l.score || 0) / (S.maxScore || 1)), collab: 0.01 }[l.kind] || 0.1));
  window.addEventListener("resize", () => S.fg.width(el.clientWidth).height(el.clientHeight));
  el.addEventListener("mousemove", (e) => {
    const h = $("#hover");
    const r = $("#stage").getBoundingClientRect();
    h.style.left = Math.min(e.clientX - r.left + 14, r.width - 300) + "px";
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
  const now = S.T;
  let html = "";
  if (n.kind === "area") {
    const owners = S.data.links.filter((l) => l.kind === "official" && idOf(l.target) === n.id).map((l) => idOf(l.source).slice(2));
    const walkers = S.data.links.filter((l) => l.kind === "worn" && idOf(l.target) === n.id && l._s > 0.002)
      .sort((a, b) => b._s - a._s).slice(0, 3).map((l) => `@${idOf(l.source).slice(2)}`);
    html = `<b>${esc(n.label)}/</b><br><span class="k">${n.prs} PRs · ${n.files ?? "?"} files</span><br>
      <span style="color:${C.blue}">Officially:</span> ${owners.length ? esc(owners.map((o) => "@" + o).join(", ")) : "<i>nobody</i>"}<br>
      <span style="color:${C.amber}">Actually walked by:</span> ${esc(walkers.join(", ") || "-")}`;
  } else if (n.kind === "team") {
    html = `<b>@${esc(n.label)}</b><br><span class="k">team in CODEOWNERS</span>`;
  } else {
    const ws = S.data.links.filter((l) => l.kind === "worn" && idOf(l.source) === n.id);
    const last = Math.max(0, ...ws.map((l) => l._last || 0));
    const owns = S.data.links.filter((l) => l.kind === "official" && idOf(l.source) === n.id).length;
    html = `<b>@${esc(n.label)}</b><br><span class="k">${n.prs ?? 0} PR touches · last active ${ago(last || null, now)}</span>
      ${owns ? `<br><span style="color:${C.blue}">Official owner of ${owns} area${owns > 1 ? "s" : ""}</span>` : ""}
      ${(n._s || 0) < 0.01 && owns ? `<br><span style="color:${C.ghost}">Ghost owner: no recent work here</span>` : ""}`;
  }
  h.innerHTML = html;
  h.style.display = "block";
}

async function onClick(n) {
  if (n.kind === "area") {
    const r = await tool("who_knows", n.label);
    applyHighlight(r.highlight, true);
    addCard(areaCard(r));
  } else if (n.kind === "person") {
    const r = await tool("person", n.label);
    applyHighlight(r.highlight, true);
    addCard(personCard(r));
  }
}

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
    n.x = k ? (x / k) * 0.7 : (Math.random() - 0.5) * 100;
    n.y = k ? (y / k) * 0.7 : (Math.random() - 0.5) * 100;
  }
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
  if (replace || !S.hl) S.hl = { nodes: new Set(), links: new Set() };
  for (const id of h.nodes || []) { const r = resolveNode(id); if (r) S.hl.nodes.add(r); }
  const existing = new Set(S.data.links.map((l) => linkKey(idOf(l.source), idOf(l.target))));
  const extra = [];
  for (const [a0, b0, kind] of h.links || []) {
    const a = resolveNode(a0), b = resolveNode(b0);
    if (!a || !b) continue;
    S.hl.nodes.add(a); S.hl.nodes.add(b);
    S.hl.links.add(linkKey(a, b));
    if (!existing.has(linkKey(a, b)) && !existing.has(linkKey(b, a)) && kind === "path") extra.push({ source: a, target: b, kind: "path" });
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
  S.fg.zoomToFit(700, 150, filter);
  setTimeout(() => { if (S.fg.zoom() > 2.4) S.fg.zoom(2.4, 400); }, 720);
}

function clearHighlight() {
  S.hl = null;
  const d = S.fg.graphData();
  S.fg.graphData({ nodes: d.nodes, links: d.links.filter((l) => l.kind !== "path") });
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
  $("#play").textContent = S.playing ? "❚❚" : "▶";
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
  const humans = S.data.nodes.filter((n) => n.kind === "person").length;
  $("#summary").innerHTML = `
    <div class="big">${cov}%<small>of files have an official owner</small></div>
    <div class="meter"><div style="width:${cov}%"></div></div>
    <div class="meter-cap"><span>blueprint</span><span>everything else is walked, not paved</span></div>
    <div class="sum-line"><b>${esc(r.repo)}</b> · ${r.stats.PR} merged PRs · ${r.stats.Person} contributors · ${fmtMonth(r.from)} to ${fmtMonth(r.to)}</div>`;
}

function renderSuggestions() {
  const areas = S.data.nodes.filter((n) => n.kind === "area" && n.depth === 2 && !n.owned).sort((a, b) => b.prs - a.prs);
  const people = S.data.nodes.filter((n) => n.kind === "person").sort((a, b) => (b._s || 0) - (a._s || 0));
  const qs = [
    areas[0] && `Who actually knows ${areas[0].label}?`,
    "Where does CODEOWNERS disagree with reality?",
    people[0] && `What breaks if @${people[0].label} goes on vacation?`,
    "Which open PRs are stuck with the wrong reviewer?",
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
    `<a href="${esc(e.url)}" target="_blank" rel="noopener" title="${esc(e.title)}">#${e.number} ${esc(e.title)}</a>`).join("")}</div>`;
}

function areaCard(r) {
  if (!r.data.actual_people) return `<div class="card"><h3>${esc(r.data.topic || "")}</h3><pre>${esc(JSON.stringify(r.data, null, 1))}</pre></div>`;
  const d = r.data;
  const off = Array.isArray(d.official_owners)
    ? d.official_owners.map((o) => `<div class="row"><span class="off">@${esc(o.owner)}</span><span class="muted">${o.active_here ? "active " + o.active_here.last : o.kind === "Team" ? "team" : "no recent work here"}</span></div>`).join("")
    : `<div class="row muted">nobody: no CODEOWNERS rule</div>`;
  const ppl = d.actual_people.map((p) => `<div><div class="row"><span>@${esc(p.login)}</span><span class="muted">${p.authored} authored · ${p.reviewed} reviewed · ${p.last_active}</span></div><div class="bar" style="width:${Math.round(p.share_of_recent_work * 100)}%"></div></div>`).join("");
  return `<div class="card"><h3>${esc(d.area)}/</h3>
    <div class="muted" style="font-size:11px;margin-bottom:4px">OFFICIALLY</div>${off}
    <div class="muted" style="font-size:11px;margin:10px 0 4px">ACTUALLY (share of recent work)</div>${ppl}
    ${evidenceHtml(r.evidence)}</div>`;
}

function personCard(r) {
  const d = r.data;
  const works = d.works_on.map((w) => `<div class="row"><span>${esc(w.area)}/</span><span class="muted">${w.prs} PRs · ${w.last}</span></div>`).join("");
  const rev = d.reviews_with.map((w) => `@${esc(w.login)} (${w.reviews})`).join(", ");
  const owns = Array.isArray(d.officially_owns) ? d.officially_owns.map((a) => esc(a)).join(", ") : esc(d.officially_owns);
  return `<div class="card"><h3>@${esc(d.login)}</h3>${works}
    <div class="row" style="margin-top:6px"><span class="muted">reviews with</span><span>${rev || "-"}</span></div>
    <div class="row"><span class="off">officially owns</span><span>${owns}</span></div></div>`;
}

/* ---------- decisions & actions ---------- */
function decisionCard(d) {
  if (typeof d === "string") return `<div class="card muted">${esc(d)}</div>`;
  const pr = d.pr;
  const dec = d.decision || {};
  const touches = (d.touches || []).map((t) => `<div class="touch"><span>${esc(t.area)}/</span><div class="tb" style="width:${Math.round(t.share_of_change * 100)}%"></div></div>`).join("");
  const hop = (c, tag, why) => c ? `<div class="hop"><span class="tagline">${tag}</span><span class="who">@${esc(c.login)}</span>
      <span class="why">${Math.round(c.share_of_recent_work_there * 100)}% of recent work in <code>${esc(c.main_area)}/</code> · ${c.prs_there} PRs · active ${esc(c.last_active)} · ${c.open_review_requests} open reviews${c.has_reviewed_author_before ? ` · reviewed @${esc(pr.author)} ${c.has_reviewed_author_before}× before` : ""}${why ? `<br><i>${esc(why)}</i>` : ""}</span></div>` : "";
  const ruled = (d.considered_and_rejected || []).map((r) => `<s>@${esc(r.login)}</s> ${esc(r.why_not)}`).join(" · ");
  const verdicts = Array.isArray(d.currently_requested)
    ? d.currently_requested.map((v) => `<div class="verdict">Requested @${esc(v.requested)}: <span class="${v.verdict === "good choice" ? "good" : "bad"}">${esc(v.verdict)}</span></div>`).join("")
    : `<div class="verdict"><span class="bad">${esc(d.currently_requested || "")}</span></div>`;
  const past = (d.past_actions_on_this_pr || []).map((a) => `${esc(a.kind)} ${esc(a.status)} ${esc(a.when)}`).join(", ");
  const act = d.proposed_action;
  const id = "act-" + Math.random().toString(36).slice(2);
  setTimeout(() => wireApprove(id, act), 0);
  return `<div class="decision">
    <div class="pr-title"><a href="${esc(pr.url)}" target="_blank" rel="noopener">#${pr.number} ${esc(pr.title)}</a></div>
    <div class="pr-meta">by @${esc(pr.author)} · opened ${esc(pr.opened)}${pr.draft ? " · draft" : ""}</div>
    <div class="muted" style="font:10px var(--mono);letter-spacing:.06em">TOUCHES</div>${touches}
    <div class="chain">${hop(dec.primary, "Primary reviewer", "")}${hop(dec.second, "Second reviewer", dec.why_second)}</div>
    ${ruled ? `<div class="ruled">Ruled out: ${ruled}</div>` : ""}
    ${verdicts}
    ${past ? `<div class="ruled">Already done: ${past}</div>` : ""}
    ${act ? `<div class="approve" id="${id}"><button>Request ${act.reviewers.map((r) => "@" + esc(r)).join(" + ")}</button><span class="note">checking permissions…</span></div>` : ""}
  </div>`;
}

async function wireApprove(id, act) {
  const box = document.getElementById(id);
  if (!box) return;
  const btn = box.querySelector("button"), note = box.querySelector(".note");
  const body = { repo: S.repo, kind: "request_reviewers", number: act.number, reviewers: act.reviewers };
  const plan = await (await fetch("/api/act", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
  note.textContent = plan.can_write ? "you have write access: this will request reviews on GitHub" : `read-only on ${S.repo}: approving records a dry run, nothing is posted`;
  btn.onclick = async () => {
    btn.disabled = true;
    note.textContent = "working…";
    const r = await (await fetch("/api/act", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...body, confirm: true }) })).json();
    note.innerHTML = r.status === "done" ? `done · <a href="${esc(r.url)}" target="_blank">view on GitHub</a>`
      : r.status === "dry-run" ? `dry run recorded in the graph: ${esc(r.reason)}` : `failed: ${esc(r.error || r.status)}`;
  };
}

function queueCard(d) {
  const rows = d.queue.map((q) => `<div class="qrow sev${q.severity}">
      <span class="num"><a href="${esc(q.url)}" target="_blank" rel="noopener">#${q.number}</a></span>
      <div class="t"><div title="${esc(q.title)}">${esc(q.title)}</div><div class="prob">${esc(q.problems[0])}</div>
        <div class="sug">${q.suggested.length ? "→ " + q.suggested.map((x) => "@" + esc(x)).join(", ") : ""}</div></div>
      <button data-n="${q.number}">Route</button></div>`).join("");
  setTimeout(() => document.querySelectorAll(".qrow button[data-n]").forEach((b) => (b.onclick = () => routeDirect(+b.dataset.n))), 0);
  const kinds = { unrouted: "nobody asked", "absent-reviewer": "absent reviewer", "wrong-reviewer": "wrong reviewer", waiting: "waiting 14d+" };
  return `<div class="decision"><div class="pr-title">Review queue triage</div>
    <div class="stats3"><div><b>${d.open_prs_checked}</b>open PRs checked</div><div><b style="color:var(--risk)">${d.flagged}</b>misrouted</div>
      ${Object.entries(d.by_problem).map(([k, v]) => `<div><b>${v}</b>${esc(kinds[k] || k)}</div>`).join("")}</div>
    <div class="queue">${rows}</div></div>`;
}

async function routeDirect(n) {
  addCard(`<div class="msg-user">Route PR #${n}</div>`);
  const r = await tool("route_pr", String(n));
  applyHighlight(r.highlight, true);
  addCard(decisionCard(r.data) + evidenceHtml(r.evidence));
}

async function triageDirect() {
  addCard(`<div class="msg-user">Triage the review queue</div>`);
  const r = await tool("triage");
  applyHighlight(r.highlight, true);
  addCard(queueCard(r.data));
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
  box.innerHTML = `<div class="walk"><div class="pending"><span class="foot">●</span><span>reading the graph…</span></div></div><div class="answer"></div><div class="cards"></div>`;
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
        if (ev.type === "model") $("#model").textContent = `agent: ${ev.provider} · ${ev.model} · graph: FalkorDB`;
        if (ev.type === "step") {
          steps.push(ev);
          walk.innerHTML = steps.map((s, k) => `<div data-k="${k}"><span class="foot">→</span><span>${esc(s.label)}</span></div>`).join("")
            + `<div class="pending"><span class="foot">●</span><span>walking…</span></div>`;
          walk.querySelectorAll("[data-k]").forEach((d) => (d.onclick = () => applyHighlight(steps[+d.dataset.k].highlight, true)));
          applyHighlight(ev.highlight);
          if (ev.tool === "route_pr" && ev.data && ev.data.pr) box.querySelector(".cards").insertAdjacentHTML("beforeend", decisionCard(ev.data));
          if (ev.tool === "triage" && ev.data && ev.data.queue) box.querySelector(".cards").insertAdjacentHTML("beforeend", queueCard(ev.data));
          if (ev.tool === "pave") box.querySelector(".cards").insertAdjacentHTML("beforeend", `<div class="approve"><button onclick="pave()">Review the CODEOWNERS draft</button></div>`);
          $("#thread").scrollTop = 1e9;
        }
        if (ev.type === "answer") {
          answer = ev.text;
          walk.querySelector(".pending")?.remove();
          if (!steps.length) walk.remove();
          box.querySelector(".answer").innerHTML = marked.parse(ev.text || "") + evidenceHtml(ev.evidence);
          box.querySelectorAll(".answer a").forEach((a) => { a.target = "_blank"; a.rel = "noopener"; });
          applyHighlight(ev.highlight, true);
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
    return `<div class="chg"><span>${esc(c.area)}/</span><span class="from ${today.length ? "" : "none"}">${today.length ? today.map((x) => "@" + esc(x)).join(" ") : "nobody"}</span><span>→</span><span class="to">${c.suggested.map((x) => "@" + esc(x)).join(" ")}</span></div>`;
  }).join("");
  $("#pave-code").textContent = "# Drafted by Desire Paths from recent authorship and review activity\n" + d.codeowners;
  $("#pave-status").textContent = "";
  $("#pave-dialog").showModal();
  markPaved(r.highlight);
  const plan = await (await fetch("/api/act", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ repo: S.repo, kind: "open_codeowners_pr" }) })).json();
  const btn = $("#pave-pr-btn");
  btn.disabled = false;
  $("#pave-status").textContent = plan.target ? `will open a PR on ${plan.target}` : `read-only on ${S.repo} and no fork: approving records a dry run`;
  btn.onclick = async () => {
    btn.disabled = true;
    $("#pave-status").textContent = "opening PR…";
    const res = await (await fetch("/api/act", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ repo: S.repo, kind: "open_codeowners_pr", confirm: true }) })).json();
    $("#pave-status").innerHTML = res.status === "done" ? `opened · <a href="${esc(res.url)}" target="_blank">${esc(res.url)}</a>` : esc(res.reason || res.error || res.status);
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
  const tops = S.data.nodes.filter((n) => n.kind === "area" && n.depth === 1).map((n) => n.prs).sort((a, b) => b - a);
  S.labelMin = tops[Math.min(13, tops.length - 1)] || 0;
  const subs = S.data.nodes.filter((n) => n.kind === "area" && n.depth === 2).map((n) => n.prs).sort((a, b) => b - a);
  S.label2Min = Math.max(3, subs[Math.min(9, subs.length - 1)] || 0);
  setTimeout(() => S.fg.zoomToFit(600, 70), 1800);
  S.fg.graphData({ nodes: S.data.nodes, links: S.data.links });
  renderSummary();
  renderSuggestions();
  $("#thread").innerHTML = "";
  $("#suggest").classList.remove("compact");
  addCard(`<div class="msg-agent" style="color:var(--ink-2);font-size:13px">
    Each <span style="color:var(--amber)">amber path</span> is worn by real pull requests and reviews; it fades when people stop walking it.
    <span style="color:var(--blue)">Dashed blue</span> is what CODEOWNERS says. Press ▶ to watch ${esc(repo)} wear in over time, click any area or person, or ask below.</div>`);
}

async function ingest(e) {
  e.preventDefault();
  const repo = $("#ingest-input").value.trim().replace(/^https?:\/\/github\.com\//, "").replace(/\/$/, "");
  if (!repo.includes("/")) return;
  addCard(`<div class="msg-agent walk"><div class="pending" id="ing"><span class="foot">●</span><span>walking ${esc(repo)}: starting…</span></div></div>`);
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
$("#copy-btn").onclick = () => navigator.clipboard.writeText($("#pave-code").textContent);
document.querySelectorAll("#layers [data-layer]").forEach((b) => (b.onclick = () => {
  S.layers[b.dataset.layer] = !S.layers[b.dataset.layer];
  b.classList.toggle("on", S.layers[b.dataset.layer]);
  refresh();
}));
fetch("/api/health").then((r) => r.json()).then((h) => ($("#model").textContent = `agent: ${h.llm} · graph: FalkorDB`));
loadRepos();
