"""Generate index.html for the Desire Paths demo film.

Scene lengths come from the measured narration, so picture and voice can never drift.
Run: python3 gen.py  (reads assets/*.wav durations and assets/rects.json)
"""

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
A = HERE / "assets"


def dur(wav: str) -> float:
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "csv=p=0", str(A / wav)])
    return round(float(out), 3)


LEAD, TAIL = 0.4, 0.6
VO = [dur(f"s{i}.wav") for i in range(1, 11)]
# Scene length = breath before the voice + the voice + a beat after. Some scenes need room for picture.
lead = [0.8] + [LEAD] * 9
length = [lead[i] + VO[i] + TAIL for i in range(10)]
length[2] = max(length[2], lead[2] + 9.4 + 5.2)   # map: the 5 s time-lapse starts at "Press play"
length[9] = lead[9] + VO[9] + 3.2                  # close: hold the title
start, t = [], 0.0
for L in length:
    start.append(round(t, 3))
    t += L
TOTAL = round(t, 3)
RECTS = json.loads((A / "rects.json").read_text())

timing = {"start": start, "lead": lead, "length": [round(x, 3) for x in length], "total": TOTAL}

FONTS = "".join(
    f'@font-face{{font-family:"{fam}";src:url("assets/fonts/{file}-{w}.woff2") format("woff2");font-weight:{w};font-style:normal;}}\n'
    for fam, file, ws in [("Plus Jakarta Sans", "plus-jakarta-sans", (600, 700, 800)),
                          ("DM Sans", "dm-sans", (400, 500, 700)),
                          ("JetBrains Mono", "jetbrains-mono", (400, 500))]
    for w in ws)


def device(sid: str, img: str, x: int, y: int, w: int, h: int, extra: str = "", img2: str = "") -> str:
    """A raised clay window showing an app screenshot through a movable camera."""
    second = f'<img id="{sid}-img2" class="shot" src="assets/{img2}" alt="">' if img2 else ""
    return f"""
      <div id="{sid}-dev" class="device" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px">
        <div class="viewport">
          <div id="{sid}-cam" class="cam" data-layout-allow-overflow>
            <img id="{sid}-img" class="shot" src="assets/{img}" alt="">{second}
            {extra}
          </div>
        </div>
      </div>"""


def scene(i: int, body: str) -> str:
    return (f'<section id="sc{i}" class="clip scene" data-start="{start[i - 1]}" data-duration="{round(length[i - 1], 3)}" '
            f'data-track-index="1"><div id="sc{i}-in" class="scene-in">{body}</div></section>')


GROOVE = "M 230 760 C 520 760, 560 420, 900 470 S 1330 300, 1690 290"
PAVED = "M 230 760 L 1690 290"
GROOVE_END = "M 160 930 C 560 940, 760 790, 1080 830 S 1480 780, 1760 760"
PAVED_END = "M 160 930 L 1760 760"

scenes = [
    # 1. Open: the paved path and the desire path
    scene(1, f"""
      <svg class="full" viewBox="0 0 1920 1080" aria-hidden="true">
        <path id="o-paved" d="{PAVED}" class="paved"/>
        <g id="o-groove">
          <path d="{GROOVE}" class="g-dark" transform="translate(-3,-3)"/>
          <path d="{GROOVE}" class="g-light" transform="translate(3,3)"/>
          <path d="{GROOVE}" class="g-floor"/>
        </g>
        <circle id="o-walker" r="9" class="walker" cx="230" cy="760"/>
      </svg>
      <div id="o-l1" class="o-line" style="top:120px">Every repository has a <span class="violet">paved path</span>.</div>
      <div id="o-l2" class="o-line" style="top:200px">And it has <b>desire paths</b>.</div>
      <div id="o-tag1" class="tag plan" style="left:1180px;top:520px">CODEOWNERS, the org chart</div>
      <div id="o-tag2" class="tag" style="left:560px;top:520px">where people actually walk</div>
      <div id="o-title" class="lockup" style="left:1180px;top:780px"><span class="mark"><span></span></span><span>Desire Paths</span></div>
    """),
    # 2. What it found
    scene(2, """
      <div id="f-eyebrow" class="eyebrow">FalkorDB/FalkorDB · 600 merged PRs · one FalkorDB graph</div>
      <div class="tiles">
        <div id="f-t1" class="tile">
          <div class="dial"><svg viewBox="0 0 200 200"><circle cx="100" cy="100" r="80" class="dial-track"/><circle id="f-arc" cx="100" cy="100" r="80" class="dial-arc"/></svg><div class="dial-core"><span class="pct"><span id="f-n1">0</span>%</span></div></div>
          <div class="tile-text">of files have an official owner</div>
        </div>
        <div id="f-t2" class="tile">
          <div class="big"><span id="f-n2" class="risk">0</span><span class="of">/40</span></div>
          <div class="tile-text">newest open PRs are misrouted</div>
          <div class="tile-sub">16 with nobody asked to review</div>
        </div>
        <div id="f-t3" class="tile">
          <div class="big"><span id="f-n3">0</span></div>
          <div class="tile-text">reviews written by AI bots</div>
          <div class="tile-sub">more than all humans combined</div>
        </div>
      </div>
    """),
    # 3. The map, then the time-lapse
    scene(3, device("m", "01-map.png", 160, 70, 1600, 900) + """
      <div id="m-c1" class="chip" style="left:120px;top:900px"><span class="sw-plan"></span>Dashed violet: what CODEOWNERS says</div>
      <div id="m-c2" class="chip" style="left:120px;top:900px"><span class="sw-groove"></span>Grooves: worn by real pull requests and reviews</div>
      <div id="m-c3" class="chip" style="left:120px;top:900px">They fade when people stop walking them</div>
      <div id="m-c4" class="chip accent" style="left:120px;top:900px">18 months of FalkorDB, worn in</div>
    """),
    # 4. Triage
    scene(4, device("q", "02-triage.png", 160, 70, 1600, 900,
                    extra='<div id="q-ring1" class="ring"></div><div id="q-ring2" class="ring"></div>') + """
      <div id="q-c1" class="chip" style="left:120px;top:900px"><b class="risk">16</b>&nbsp;pull requests, nobody asked to review</div>
      <div id="q-c2" class="chip" style="left:120px;top:900px">#2647 has waited&nbsp;<b class="risk">53 days</b></div>
    """),
    # 5. Route and approve
    scene(5, device("r", "03-route.png", 700, 150, 1120, 630, img2="04-approved.png",
                    extra='<div id="r-ring" class="ring"></div><div id="r-tap" class="tap"></div>') + """
      <div class="hops">
        <div id="r-h1" class="hop"><span class="k">PR</span>#2647 fix(runtime)</div>
        <div id="r-a1" class="arrow"></div>
        <div id="r-h2" class="hop"><span class="k">code</span>graph/src/runtime/</div>
        <div id="r-a2" class="arrow"></div>
        <div id="r-h3" class="hop"><span class="k">people</span>@AviAvni · 51% of recent work</div>
        <div id="r-a3" class="arrow"></div>
        <div id="r-h4" class="hop"><span class="k">load</span>3 open review requests</div>
      </div>
      <div id="r-c1" class="chip" style="left:700px;top:850px">Picks two reviewers, says who it ruled out</div>
      <div id="r-c2" class="chip accent" style="left:700px;top:850px">One click to approve</div>
    """),
    # 6. What if someone is away
    scene(6, device("g", "05-gone.png", 160, 70, 1600, 900) + """
      <div id="g-q" class="chip" style="left:120px;top:900px">What breaks if <b>@DvirDukhan</b> goes on vacation?</div>
      <div id="g-n1" class="stat-pop" style="left:120px;top:820px"><b class="risk">27</b><span>open PRs lose their reviewer</span></div>
      <div id="g-n2" class="stat-pop" style="left:820px;top:820px"><b class="risk">21</b><span>have no good backup</span></div>
    """),
    # 7. Three ways to route, then the backtest
    scene(7, device("c", "06-compare.png", 160, 70, 1600, 900,
                    extra='<div id="c-ring" class="ring"></div>') + """
      <div id="c-c1" class="chip" style="left:120px;top:900px">CODEOWNERS names&nbsp;<b class="risk">no one</b>: 0 of 40</div>
      <div id="c-c2" class="chip" style="left:120px;top:900px">The graph names an expert for&nbsp;<b>38 of 40</b></div>
      <div id="c-c3" class="chip accent" style="left:120px;top:900px">algo.maxFlow: busiest load 31 → 16</div>
      <div id="c-bt" class="backtest">
        <div class="bt-title">Replayed on 300 merged PRs: did it pick someone who really reviewed?</div>
        <div class="bt-row"><span class="bt-label">CODEOWNERS</span><div class="bt-bar"><span id="c-b1"></span></div><span id="c-v1" class="bt-val">0%</span></div>
        <div class="bt-row"><span class="bt-label">Two busiest reviewers</span><div class="bt-bar"><span id="c-b2"></span></div><span id="c-v2" class="bt-val">0%</span></div>
        <div class="bt-row win"><span class="bt-label">Desire paths</span><div class="bt-bar"><span id="c-b3"></span></div><span id="c-v3" class="bt-val">0%</span></div>
        <div class="bt-foot">Top two picks · only activity from before each PR · on Apache Airflow: 44% vs 26% for CODEOWNERS</div>
      </div>
    """),
    # 8. Proof and MCP
    scene(8, device("p", "07-proof.png", 90, 150, 860, 760) + """
      <div id="p-term" class="term">
        <div class="term-head"><i></i><i></i><i></i><span>any agent · MCP</span></div>
        <pre id="p-code"><span class="prompt">→</span> tools/call <b>route_pr</b>
  {"repo": "FalkorDB/FalkorDB", "number": 2647}

<span class="dim">←</span> {
    "primary":  "@AviAvni",
    "main_area": "graph/src/runtime",
    "share_of_recent_work": 0.51,
    "second":   "@DvirDukhan",
    "why_second": "knowledge spreading",
    "proposed_action": "request_reviewers"
  }</pre>
      </div>
      <div id="p-c1" class="chip" style="left:90px;top:960px">The Cypher behind every answer</div>
      <div id="p-c2" class="chip accent" style="left:1010px;top:960px">17 tools over MCP</div>
    """),
    # 9. The real pull request
    scene(9, device("x", "09-pr-files.png", 160, 70, 1600, 900) + """
      <div id="x-c1" class="chip" style="left:120px;top:900px">github.com/addu2612/FalkorDB/pull/1</div>
      <div id="x-c2" class="chip accent" style="left:120px;top:900px">+23 −0 · existing rules kept, and still win</div>
    """),
    # 10. Close
    scene(10, f"""
      <svg class="full" viewBox="0 0 1920 1080" aria-hidden="true">
        <path d="{PAVED_END}" class="paved faint"/>
        <g id="e-groove">
          <path d="{GROOVE_END}" class="g-dark" transform="translate(-3,-3)"/>
          <path d="{GROOVE_END}" class="g-light" transform="translate(3,3)"/>
          <path d="{GROOVE_END}" class="g-floor violet-floor"/>
        </g>
      </svg>
      <div id="e-lock" class="lockup center"><span class="mark"><span></span></span><span>Desire Paths</span></div>
      <div id="e-sub" class="e-sub">The org chart is the paved path. This is where people actually walk.</div>
      <div id="e-meta" class="e-meta">Built on FalkorDB · Graph Hacks, Track 01 · github.com/addu2612/Desire-Paths</div>
    """),
]

audio = "\n".join(
    f'<audio id="vo{i + 1}" src="assets/s{i + 1}.wav" data-start="{round(start[i] + lead[i], 3)}" '
    f'data-duration="{VO[i]}" data-track-index="{10}" data-volume="1"></audio>' for i in range(10))

lapse_start = round(start[2] + lead[2] + 9.4, 3)
# The time-lapse plays in the same window as scene 3's device, over the full-app camera view.
lapse = (f'<div id="lapse-wrap" style="position:absolute;left:160px;top:70px;width:1600px;height:900px;'
         f'border-radius:28px;overflow:hidden;opacity:0;z-index:5">'
         f'<video id="lapse" class="clip" src="assets/lapse.mp4" muted playsinline data-start="{lapse_start}" '
         f'data-duration="5" data-track-index="2" style="width:100%;height:100%;object-fit:cover"></video></div>')

CSS = (HERE / "film.css").read_text()
JS = (HERE / "film.js").read_text()

html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=1920, height=1080" />
<title>Desire Paths: demo</title>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
{FONTS}
{CSS}
</style>
</head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{TOTAL}" data-width="1920" data-height="1080">
{"".join(scenes)}
{lapse}
{audio}
</div>
<script>
const T = {json.dumps(timing)};
const R = {json.dumps(RECTS)};
{JS}
</script>
</body>
</html>
"""
(HERE / "index.html").write_text(html)
print("total", TOTAL, "starts", start)
