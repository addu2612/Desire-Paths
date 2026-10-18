// Desire Paths demo film. T (scene timing) and R (element rectangles in the app screenshots) are injected by gen.py.
const tl = gsap.timeline({ paused: true });
const S = (i, t) => T.start[i - 1] + T.lead[i - 1] + t; // seconds after scene i's narration begins
const B = (i, t) => T.start[i - 1] + t;                // seconds after scene i begins
const END = (i) => T.start[i - 1] + T.length[i - 1];
const $ = (s) => document.querySelector(s);

/* ---------- camera: frame a rectangle of the 1920x1080 app screenshot inside a device window ---------- */
const FULL = { x: 0, y: 0, w: 1920, h: 1080 };
function camState(dev, rect, pad = 1.08, kmax = 1.55) {
  const el = $(dev);
  const VW = parseFloat(el.style.width) - 28, VH = parseFloat(el.style.height) - 28;
  const k0 = Math.max(VW / 1920, VH / 1080);
  const k = Math.min(kmax, Math.max(k0, Math.min(VW / (rect.w * pad), VH / (rect.h * pad))));
  const cx = rect.x + rect.w / 2, cy = rect.y + rect.h / 2;
  const x = Math.min(0, Math.max(VW - 1920 * k, VW / 2 - cx * k));
  const y = Math.min(0, Math.max(VH - 1080 * k, VH / 2 - cy * k));
  return { x, y, scale: k };
}
function camSet(id, rect, at, pad, kmax) { tl.set(`#${id}-cam`, camState(`#${id}-dev`, rect, pad, kmax), at); }
function camTo(id, rect, at, dur = 1.1, pad, kmax) {
  tl.to(`#${id}-cam`, { ...camState(`#${id}-dev`, rect, pad, kmax), duration: dur, ease: "power3.inOut" }, at);
}
const grow = (r, m) => ({ x: r.x - m, y: r.y - m, w: r.w + 2 * m, h: r.h + 2 * m });

/* ---------- small vocabulary ---------- */
function show(sel, at, until, from = { y: 24 }) {
  tl.fromTo(sel, { opacity: 0, ...from }, { opacity: 1, y: 0, x: 0, duration: 0.45, ease: "power3.out" }, at);
  if (until != null) tl.to(sel, { opacity: 0, y: -10, duration: 0.3, ease: "power2.in" }, until);
}
function ring(sel, rect, at, until) {
  tl.set(sel, { left: rect.x, top: rect.y, width: rect.w, height: rect.h }, at - 0.01);
  tl.fromTo(sel, { opacity: 0, scale: 1.08 }, { opacity: 1, scale: 1, duration: 0.4, ease: "power3.out" }, at);
  if (until != null) tl.to(sel, { opacity: 0, duration: 0.3 }, until);
}
function count(sel, to, at, dur = 0.9, fmt = (v) => String(Math.round(v))) {
  const o = { v: 0 };
  tl.set(sel, { textContent: fmt(0) }, at - 0.01);
  tl.to(o, { v: to, duration: dur, ease: "power2.out", onUpdate: () => { $(sel).textContent = fmt(o.v); } }, at);
}
function draw(paths, at, dur) {
  document.querySelectorAll(paths).forEach((p) => {
    const L = p.getTotalLength();
    tl.fromTo(p, { strokeDasharray: L, strokeDashoffset: L }, { strokeDashoffset: 0, duration: dur, ease: "power2.inOut" }, at);
  });
}

/* scene entrances and exits: content settles out of the clay */
for (let i = 1; i <= 10; i++) {
  tl.fromTo(`#sc${i}-in`, { opacity: 0 }, { opacity: 1, duration: 0.4, ease: "power2.out" }, B(i, 0));
  if (i < 10) tl.to(`#sc${i}-in`, { opacity: 0, duration: 0.3, ease: "power2.in" }, END(i) - 0.3);
}

/* 1. Open */
show("#o-l1", S(1, 0.0), null, { y: 30 });
tl.fromTo("#o-paved", { clipPath: "inset(0 100% 0 0)" }, { clipPath: "inset(0 0% 0 0)", duration: 1.2, ease: "power2.inOut" }, S(1, 1.4));
show("#o-tag1", S(1, 2.6), null);
show("#o-l2", S(1, 4.5), null, { y: 30 });
draw("#o-groove path", S(1, 4.8), 1.8);
{
  const g = $("#o-groove .g-floor"), L = g.getTotalLength(), o = { p: 0 };
  tl.set("#o-walker", { opacity: 0 }, B(1, 0));
  tl.set("#o-walker", { opacity: 1 }, S(1, 4.8));
  tl.to(o, { p: 1, duration: 1.8, ease: "power2.inOut", onUpdate: () => {
    const pt = g.getPointAtLength(o.p * L);
    $("#o-walker").setAttribute("cx", pt.x); $("#o-walker").setAttribute("cy", pt.y);
  } }, S(1, 4.8));
}
show("#o-tag2", S(1, 5.8), null);
show("#o-title", S(1, 6.6), null, { y: 40 });

/* 2. What it found */
show("#f-eyebrow", S(2, 0.3), null);
show("#f-t1", S(2, 4.8), null, { y: 60 });
tl.fromTo("#f-arc", { strokeDashoffset: 502.65 }, { strokeDashoffset: 502.65 * (1 - 0.14), duration: 1.0, ease: "power2.out" }, S(2, 5.0));
count("#f-n1", 14, S(2, 5.0), 1.0);
show("#f-t2", S(2, 8.1), null, { y: 60 });
count("#f-n2", 17, S(2, 8.2), 0.9);
show("#f-t3", S(2, 12.1), null, { y: 60 });
count("#f-n3", 1600, S(2, 12.3), 1.4, (v) => (v >= 1599.5 ? "1,600+" : Math.round(v).toLocaleString("en-US")));

/* 3. The map, then the time-lapse */
camSet("m", FULL, B(3, 0));
camTo("m", R["01-map"].stage, S(3, 0.9), 1.3, 1.0);
show("#m-c1", S(3, 1.1), S(3, 3.4));
show("#m-c2", S(3, 3.6), S(3, 6.7));
show("#m-c3", S(3, 6.9), S(3, 9.0));
camTo("m", FULL, S(3, 8.5), 0.8);
tl.fromTo("#lapse-wrap", { opacity: 0 }, { opacity: 1, duration: 0.35 }, S(3, 9.4));
tl.to("#lapse-wrap", { opacity: 0, duration: 0.3 }, END(3) - 0.3);
show("#m-c4", S(3, 9.7), null);

/* 4. Triage */
const qCard = { x: 1345, y: 262, w: 414, h: 520 };
const qStats = R["02-triage"].stats;
camSet("q", FULL, B(4, 0));
camTo("q", qCard, S(4, 0.6), 1.2, 1.02);
ring("#q-ring1", grow(qStats, 8), S(4, 3.4), S(4, 6.9));
show("#q-c1", S(4, 3.4), S(4, 6.8));
ring("#q-ring2", { x: 1372, y: qStats.y + qStats.h + 14, w: 360, h: 92 }, S(4, 7.1), null);
show("#q-c2", S(4, 7.0), null);

/* 5. Route and approve */
for (const n of [1, 2, 3]) tl.set(`#r-a${n}`, { scaleY: 0 }, B(5, 0));
const rCard = R["03-route"].card, rChain = R["03-route"].chain, rApprove = R["03-route"].approve;
camSet("r", { x: rCard.x, y: rCard.y, w: rCard.w, h: 380 }, B(5, 0), 1.04);
show("#r-h1", S(5, 2.1), null, { x: -30, y: 0 });
tl.fromTo("#r-a1", { scaleY: 0 }, { scaleY: 1, duration: 0.3, ease: "power2.out" }, S(5, 3.0));
show("#r-h2", S(5, 3.3), null, { x: -30, y: 0 });
tl.fromTo("#r-a2", { scaleY: 0 }, { scaleY: 1, duration: 0.3, ease: "power2.out" }, S(5, 4.4));
show("#r-h3", S(5, 4.7), null, { x: -30, y: 0 });
tl.fromTo("#r-a3", { scaleY: 0 }, { scaleY: 1, duration: 0.3, ease: "power2.out" }, S(5, 6.0));
show("#r-h4", S(5, 6.3), null, { x: -30, y: 0 });
camTo("r", rChain, S(5, 7.7), 1.1, 1.1);
ring("#r-ring", grow(rChain, 6), S(5, 8.0), S(5, 10.0));
show("#r-c1", S(5, 8.0), S(5, 10.2));
tl.fromTo("#r-img2", { opacity: 0 }, { opacity: 1, duration: 0.35 }, S(5, 10.2));
camTo("r", rApprove, S(5, 10.0), 0.9, 1.4);
tl.set("#r-tap", { left: 1512, top: 808 }, B(5, 0));
tl.fromTo("#r-tap", { opacity: 1, scale: 0.4 }, { opacity: 0, scale: 1.8, duration: 0.6, ease: "power2.out" }, S(5, 11.0));
show("#r-c2", S(5, 10.6), null);

/* 6. What if someone is away */
camSet("g", FULL, B(6, 0));
show("#g-q", S(6, 0.1), S(6, 3.3));
camTo("g", { x: 250, y: 240, w: 880, h: 560 }, S(6, 3.3), 1.3, 1.0);
camTo("g", { x: 1350, y: 262, w: 420, h: 430 }, S(6, 6.3), 1.1, 1.0);
show("#g-n1", S(6, 6.7), null, { y: 40 });
show("#g-n2", S(6, 10.0), null, { y: 40 });

/* 7. Three ways to route, then the backtest */
camSet("c", FULL, B(7, 0));
camTo("c", R["06-compare"].table, S(7, 0.5), 1.2, 1.1);
show("#c-c1", S(7, 1.7), S(7, 3.3));
show("#c-c2", S(7, 3.5), S(7, 6.2));
show("#c-c3", S(7, 6.4), S(7, 12.2));
tl.to("#c-dev", { opacity: 0, scale: 0.97, duration: 0.4, ease: "power2.in" }, S(7, 12.2));
tl.fromTo("#c-bt", { opacity: 0, y: 40 }, { opacity: 1, y: 0, duration: 0.5, ease: "power3.out" }, S(7, 12.5));
tl.set("#c-b1", { scaleX: 0 }, B(7, 0));
tl.fromTo("#c-b2", { scaleX: 0 }, { scaleX: 0.73, duration: 1.1, ease: "power2.out" }, S(7, 13.5));
count("#c-v2", 73, S(7, 13.5), 1.1, (v) => Math.round(v) + "%");
tl.fromTo("#c-b3", { scaleX: 0 }, { scaleX: 0.77, duration: 1.2, ease: "power3.out" }, S(7, 16.3));
count("#c-v3", 77, S(7, 16.3), 1.2, (v) => Math.round(v) + "%");

/* 8. Proof and MCP */
const pRect = { x: 1358, y: 300, w: 388, h: 380 };
camSet("p", pRect, B(8, 0), 1.04);
camTo("p", { ...pRect, y: 520 }, B(8, 0.4), T.length[7] - 0.6, 1.04);
show("#p-c1", S(8, 0.2), null);
tl.fromTo("#p-term", { opacity: 0, x: 40 }, { opacity: 1, x: 0, duration: 0.5, ease: "power3.out" }, S(8, 2.8));
tl.fromTo("#p-code", { clipPath: "inset(0 0 100% 0)" }, { clipPath: "inset(0 0 0% 0)", duration: 1.8, ease: "none" }, S(8, 3.1));
show("#p-c2", S(8, 4.8), null);

/* 9. The real pull request */
camSet("x", FULL, B(9, 0));
camTo("x", { x: 0, y: 170, w: 1150, h: 640 }, S(9, 1.3), 1.3, 1.0);
show("#x-c1", S(9, 0.2), S(9, 3.8));
show("#x-c2", S(9, 4.0), null);

/* 10. Close */
draw("#e-groove path", B(10, 0.1), 1.6);
show("#e-lock", S(10, 0.0), null, { y: 40 });
show("#e-sub", S(10, 1.2), null);
show("#e-meta", S(10, 2.2), null);

window.__timelines["main"] = tl;
