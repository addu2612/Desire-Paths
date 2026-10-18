"""HTTP API + static UI."""

from __future__ import annotations

import contextlib
import json
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from desire_paths import actions as A  # noqa: E402
from desire_paths import graph as G  # noqa: E402
from desire_paths.agent import Agent, make_client  # noqa: E402
from desire_paths.github import load_snapshot  # noqa: E402
from desire_paths.mcp_server import mcp  # noqa: E402
from desire_paths.tools import GraphTools  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

WEB = Path(__file__).resolve().parents[2] / "web"
# The MCP endpoint lives at /mcp on the same server, so a deployed URL works in any MCP client.
mcp_app = mcp.streamable_http_app(
    streamable_http_path="/mcp", stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@contextlib.asynccontextmanager
async def lifespan(_app):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Desire Paths", lifespan=lifespan)
db = G.connect()
ingest_status: dict[str, dict] = {}


def repo_graph(repo: str):
    name = G.graph_name(repo)
    if name not in db.list_graphs():
        raise HTTPException(404, f"{repo} is not loaded")
    return db.select_graph(name)


@app.get("/api/repos")
def repos():
    out = []
    for name in sorted(db.list_graphs()):
        if "__" not in name:
            continue
        g = db.select_graph(name)
        try:
            st = G.stats(g)
            cov = g.ro_query("MATCH (r:Area {path: '/'}) OPTIONAL MATCH ()-[o:OFFICIAL]->(r) "
                             "RETURN max(o.coverage)").result_set[0][0]
            span = g.ro_query("MATCH (x:PR) RETURN min(x.merged), max(x.merged)").result_set[0]
        except Exception:
            continue
        out.append({"repo": name.replace("__", "/", 1), "stats": st, "coverage": cov or 0,
                    "from": span[0], "to": span[1]})
    return out


@app.get("/api/graph/{owner}/{name}")
def graph_view(owner: str, name: str, max_people: int = 60):
    g = repo_graph(f"{owner}/{name}")
    q = lambda s, p=None: g.ro_query(s, p or {}).result_set  # noqa: E731
    areas = q("MATCH (a:Area) WHERE a.depth >= 1 AND a.depth <= 2 AND (a.prs >= 2 OR ()-[:OFFICIAL]->(a)) "
              "RETURN a.path, a.name, a.depth, coalesce(a.prs, 0), a.files, a.last")
    area_set = {a[0] for a in areas}
    # People: the most active now, plus the most active ever (so the time-lapse shows people who left).
    people = q("""
        MATCH (p:Human)-[w:WORN]->(a:Area) WHERE a.depth >= 1 AND a.depth <= 2
        WITH p, sum(w.score) AS s, sum(w.prs) AS n, max(w.last) AS last
        RETURN p.login, s, n, last
    """)
    by_now = sorted(people, key=lambda r: -r[1])[: max_people * 2 // 3]
    by_ever = sorted(people, key=lambda r: -r[2])[: max_people // 3]
    people = list({r[0]: r for r in by_now + by_ever}.values())
    all_worn = q("""
        MATCH (p:Human)-[w:WORN]->(a:Area) WHERE a.depth >= 1 AND a.depth <= 2 AND p.login IN $logins
        RETURN p.login, a.path, w.score, w.prs, w.last, w.authored
    """, {"logins": [p[0] for p in people]})
    # Draw paths only to the most specific visible area: a top-level folder is a destination
    # only when none of its subfolders are shown, otherwise every walk would be drawn twice.
    shown_parents = {p.rsplit("/", 1)[0] for p in area_set if "/" in p}
    all_worn = [r for r in all_worn if r[1] in area_set and r[1] not in shown_parents]
    # Per area keep the top walkers now and the top walkers ever; per person keep their main paths.
    per_area: dict[str, list] = {}
    for row in all_worn:
        per_area.setdefault(row[1], []).append(row)
    kept = []
    for rows in per_area.values():
        keep = sorted(rows, key=lambda r: -r[2])[:3] + sorted(rows, key=lambda r: -r[3])[:2]
        kept += list({r[0]: r for r in keep}.values())
    per_person: dict[str, list] = {}
    for row in kept:
        per_person.setdefault(row[0], []).append(row)
    worn = []
    for rows in per_person.values():
        keep = sorted(rows, key=lambda r: -r[2])[:5] + sorted(rows, key=lambda r: -r[3])[:2]
        worn += list({r[1]: r for r in keep}.values())
    # Dated activity for each kept path, for the client-side time-lapse.
    events: dict[tuple, list] = {}
    for login, path, ts in q("""
        UNWIND $pairs AS pr
        MATCH (p:Human {login: pr[0]})-[r:AUTHORED|REVIEWED|MERGED]->(x:PR)-[:IN_AREA]->(:Area {path: pr[1]})
        WITH p, pr, x, max(CASE type(r) WHEN 'AUTHORED' THEN 1.0 WHEN 'REVIEWED' THEN
             CASE r.state WHEN 'APPROVED' THEN 0.6 WHEN 'CHANGES_REQUESTED' THEN 0.7 ELSE 0.35 END
             ELSE 0.2 END) AS w
        RETURN pr[0], pr[1], collect([x.merged, w])
    """, {"pairs": [[r[0], r[1]] for r in worn]}):
        events[(login, path)] = sorted(ts)
    official = q("""
        MATCH (o)-[r:OFFICIAL]->(a:Area) WHERE a.depth >= 1 AND a.depth <= 2 AND r.coverage >= 0.3
        RETURN labels(o)[0], coalesce(o.slug, o.login), a.path, r.coverage
    """)
    collab = q("""
        MATCH (a:Human)-[c:COLLAB]->(b:Human) WHERE a.login IN $logins AND b.login IN $logins AND c.n >= 3
        RETURN a.login, b.login, c.n, c.last
    """, {"logins": [p[0] for p in people]})

    nodes = {f"a:{p}": {"id": f"a:{p}", "kind": "area", "label": p, "name": n, "depth": d, "prs": prs,
                        "files": f, "last": last, "owned": False}
             for p, n, d, prs, f, last in areas}
    for login, s, n, last in people:
        nodes[f"p:{login}"] = {"id": f"p:{login}", "kind": "person", "label": login, "score": s, "prs": n,
                               "last": last}
    links = []
    for login, path, s, n, last, au in worn:
        if path in area_set and f"p:{login}" in nodes:
            links.append({"source": f"p:{login}", "target": f"a:{path}", "kind": "worn", "score": s,
                          "prs": n, "last": last, "authored": au, "events": events.get((login, path), [])})
    for lbl, owner, path, cov in official:
        if path not in area_set:
            continue
        oid = f"t:{owner}" if lbl == "Team" else f"p:{owner}"
        if oid not in nodes:
            nodes[oid] = {"id": oid, "kind": "team" if lbl == "Team" else "person", "label": owner,
                          "score": 0, "last": None, "ghost": lbl != "Team"}
        nodes[f"a:{path}"]["owned"] = True
        links.append({"source": oid, "target": f"a:{path}", "kind": "official", "coverage": cov})
    for path, *_ in areas:
        if "/" in path and path.rsplit("/", 1)[0] in area_set:
            links.append({"source": f"a:{path}", "target": f"a:{path.rsplit('/', 1)[0]}", "kind": "tree"})
    for a, b, n, last in collab:
        links.append({"source": f"p:{a}", "target": f"p:{b}", "kind": "collab", "n": n, "last": last})
    return {"nodes": list(nodes.values()), "links": links, "now": int(time.time())}


@app.get("/api/tool/{owner}/{name}/{tool}")
def run_tool(owner: str, name: str, tool: str, arg: str | None = None, arg2: str | None = None):
    tools = GraphTools(repo_graph(f"{owner}/{name}"), f"{owner}/{name}")
    allowed = {"who_knows", "person", "bus_factor", "hidden_bottlenecks", "blueprint_drift", "pave",
               "silos", "influence", "intro_path", "who_knows_topic", "route_pr", "triage"}
    if tool not in allowed:
        raise HTTPException(400, "unknown tool")
    args = [a for a in (arg, arg2) if a is not None]
    return getattr(tools, tool)(*args)


class Ask(BaseModel):
    repo: str
    question: str
    history: list[dict] = []


@app.post("/api/ask")
def ask(body: Ask):
    g = repo_graph(body.repo)

    def stream():
        try:
            agent = Agent(g, body.repo)
            yield f"data: {json.dumps({'type': 'model', 'model': agent.model, 'provider': agent.provider})}\n\n"
            for event in agent.run(body.question, body.history):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class Act(BaseModel):
    repo: str
    kind: str  # request_reviewers | open_codeowners_pr
    number: int | None = None
    reviewers: list[str] = []
    confirm: bool = False


@app.post("/api/act")
def act(body: Act):
    """Human-approved actions. Without confirm=true this only returns the plan."""
    g = repo_graph(body.repo)
    tools = GraphTools(g, body.repo)
    try:
        if body.kind == "request_reviewers":
            if not body.number:
                raise HTTPException(400, "number is required")
            decision = tools.route_pr(body.number, live=False)["data"]
            reviewers = body.reviewers or decision["proposed_action"]["reviewers"]
            return A.request_reviewers(g, body.repo, body.number, reviewers, decision, body.confirm)
        if body.kind == "open_codeowners_pr":
            paved = tools.pave()["data"]
            header = "# Drafted by Desire Paths from recent authorship and review activity\n"
            return A.open_codeowners_pr(g, body.repo, header + paved["codeowners"] + "\n",
                                        paved["changes_vs_today"], body.confirm)
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "failed", "error": str(e)}
    raise HTTPException(400, "unknown action")


class Ingest(BaseModel):
    repo: str
    limit: int = 300


@app.post("/api/ingest")
def ingest(body: Ingest):
    repo = body.repo.strip().removeprefix("https://github.com/").strip("/")
    if repo.count("/") != 1:
        raise HTTPException(400, "use owner/name")
    if ingest_status.get(repo, {}).get("state") == "running":
        return ingest_status[repo]

    def work():
        try:
            ingest_status[repo] = {"repo": repo, "state": "running", "step": "fetching from GitHub"}
            snap = load_snapshot(repo, refresh=True, limit=min(body.limit, 600))
            ingest_status[repo] = {"repo": repo, "state": "running", "step": "building graph"}
            ingest_status[repo] = {"repo": repo, "state": "done", "stats": G.build(snap, db)}
        except Exception as e:
            ingest_status[repo] = {"repo": repo, "state": "error", "error": str(e)}

    threading.Thread(target=work, daemon=True).start()
    ingest_status[repo] = {"repo": repo, "state": "running", "step": "starting"}
    return ingest_status[repo]


@app.get("/api/ingest/{owner}/{name}")
def ingest_state(owner: str, name: str):
    return ingest_status.get(f"{owner}/{name}", {"state": "unknown"})


@app.get("/api/health")
def health():
    try:
        _, model, provider = make_client()
        llm = f"{provider}:{model}"
    except Exception as e:
        llm = f"not configured ({e})"
    return {"falkordb": db.connection.ping(), "llm": llm}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
app.mount("/", mcp_app)  # last: serves /mcp


def main():
    import os

    import uvicorn

    uvicorn.run("desire_paths.server:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
