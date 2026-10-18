"""Build the Desire Paths graph in FalkorDB.

Two layers live in one graph:
  * Blueprint - what CODEOWNERS says:   (:Team|:Person)-[:OWNS]->(:File), rolled up to -[:OFFICIAL]->(:Area)
  * Activity  - what actually happened: (:Person)-[:AUTHORED|REVIEWED|MERGED]->(:PR)-[:TOUCHED]->(:File)
Desire paths are derived from activity with Cypher, using recency decay:
  (:Person)-[:WORN]->(:Area)      how much someone has recently worked on an area
  (:Person)-[:COLLAB]->(:Person)  reviewer -> author relationships
"""

from __future__ import annotations

import fnmatch
import os
import re
import time
from datetime import datetime

from falkordb import FalkorDB

AREA_DEPTH = 3  # areas are directories up to this many levels deep
HALF_LIFE_DAYS = 120  # an activity counts half as much after this long
BOT_PATTERNS = re.compile(
    r"(\[bot\]$|bot$|^dependabot|^copilot|^github-|coderabbit|-ai$|^kaicontext$|^overcut|^cubic|^renovate"
    r"|code-assist|code-agent|^augmentcode$|^typo-app$|^sourcery|^codecov|^sonarcloud|^mergify|^netlify|^vercel"
    r"|^greptile|^ellipsis-dev$|^qodo)",
    re.I,
)


def is_bot(login: str) -> bool:
    return bool(BOT_PATTERNS.search(login))


def graph_name(repo: str) -> str:
    return repo.replace("/", "__")


def connect() -> FalkorDB:
    return FalkorDB(
        host=os.environ.get("FALKORDB_HOST", "localhost"),
        port=int(os.environ.get("FALKORDB_PORT", "6379")),
        username=os.environ.get("FALKORDB_USERNAME") or None,
        password=os.environ.get("FALKORDB_PASSWORD") or None,
    )


def epoch(ts: str | None) -> int | None:
    if not ts:
        return None
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())


def areas_of(path: str) -> list[str]:
    """All ancestor directories of a file, up to AREA_DEPTH, root first."""
    parts = path.split("/")[:-1][:AREA_DEPTH]
    return ["/"] + ["/".join(parts[: i + 1]) for i in range(len(parts))]


def parse_codeowners(text: str) -> list[tuple[str, list[str]]]:
    rules = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, *owners = line.split()
        if owners:
            rules.append((pattern, [o.lstrip("@") for o in owners]))
    return rules


def codeowners_match(pattern: str, path: str) -> bool:
    anchored = pattern.startswith("/")
    pat = pattern.strip("/")
    if pattern.endswith("/"):
        # directory: matches everything beneath it
        if anchored:
            return path.startswith(pat + "/")
        return path.startswith(pat + "/") or f"/{pat}/" in f"/{path}"
    if "*" in pat or "?" in pat:
        if anchored or "/" in pat:
            return fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(path, pat + "/*")
        return fnmatch.fnmatch(path.rsplit("/", 1)[-1], pat) or fnmatch.fnmatch(path, f"*/{pat}")
    if anchored or "/" in pat:
        return path == pat or path.startswith(pat + "/")
    return path == pat or path.endswith("/" + pat) or f"/{pat}/" in f"/{path}" or path.startswith(pat + "/")


def file_owners(rules: list[tuple[str, list[str]]], path: str) -> list[str]:
    """GitHub semantics: the last matching rule wins."""
    owners: list[str] = []
    for pattern, rule_owners in rules:
        if codeowners_match(pattern, path):
            owners = rule_owners
    return owners


def _batches(rows: list, size: int = 2000):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def build(snapshot: dict, db: FalkorDB | None = None) -> dict:
    db = db or connect()
    name = graph_name(snapshot["repo"])
    g = db.select_graph(name)
    try:
        g.delete()
    except Exception:
        pass
    g = db.select_graph(name)

    people: dict[str, bool] = {}
    prs, authored, reviewed, merged, touched, closes, opened, issues = [], [], [], [], [], [], [], {}
    files: set[str] = set()

    for pr in snapshot["prs"]:
        n = pr["number"]
        prs.append({
            "number": n, "title": pr["title"], "url": pr["url"],
            "created": epoch(pr["createdAt"]), "merged": epoch(pr["mergedAt"]),
            "additions": pr["additions"], "deletions": pr["deletions"],
        })
        if pr["author"]:
            people[pr["author"]["login"]] = True
            authored.append({"login": pr["author"]["login"], "pr": n})
        if pr.get("mergedBy"):
            people[pr["mergedBy"]["login"]] = True
            merged.append({"login": pr["mergedBy"]["login"], "pr": n})
        for rv in pr["reviews"]["nodes"]:
            if rv["author"]:
                people[rv["author"]["login"]] = True
                reviewed.append({"login": rv["author"]["login"], "pr": n,
                                 "state": rv["state"], "at": epoch(rv["submittedAt"])})
        for f in pr["files"]["nodes"]:
            files.add(f["path"])
            touched.append({"pr": n, "path": f["path"], "add": f["additions"], "del": f["deletions"]})
        for iss in pr["closingIssuesReferences"]["nodes"]:
            issues[iss["number"]] = {"number": iss["number"], "title": iss["title"],
                                     "url": iss["url"], "created": epoch(iss["createdAt"])}
            closes.append({"pr": n, "issue": iss["number"]})
            if iss["author"]:
                people[iss["author"]["login"]] = True
                opened.append({"login": iss["author"]["login"], "issue": iss["number"]})

    area_set = {a for f in files for a in areas_of(f)}
    area_rows = [{"path": a, "depth": 0 if a == "/" else a.count("/") + 1,
                  "name": a.rsplit("/", 1)[-1] if a != "/" else "(root)"} for a in area_set]
    area_parent = [{"child": a, "parent": "/" if "/" not in a else a.rsplit("/", 1)[0]}
                   for a in area_set if a != "/"]
    file_rows = [{"path": f, "area": areas_of(f)[-1]} for f in files]

    rules = parse_codeowners(snapshot.get("codeowners", ""))
    owns = []
    for f in files:
        for owner in file_owners(rules, f):
            owns.append({"owner": owner, "path": f})
    owner_names = {o["owner"] for o in owns} | {o for _, os_ in rules for o in os_}

    for q in [
        "CREATE INDEX FOR (p:Person) ON (p.login)",
        "CREATE INDEX FOR (t:Team) ON (t.slug)",
        "CREATE INDEX FOR (r:PR) ON (r.number)",
        "CREATE INDEX FOR (i:Issue) ON (i.number)",
        "CREATE INDEX FOR (f:File) ON (f.path)",
        "CREATE INDEX FOR (a:Area) ON (a.path)",
    ]:
        g.query(q)
    # Full-text search lets the agent find topics ("vector index") that don't map to one directory.
    g.query("CALL db.idx.fulltext.createNodeIndex('PR', 'title')")
    g.query("CALL db.idx.fulltext.createNodeIndex('Issue', 'title')")

    def run(query: str, rows: list):
        for batch in _batches(rows):
            g.query(query, {"rows": batch})

    # Humans also get a :Human label so graph algorithms can be scoped to people only.
    run("UNWIND $rows AS r CREATE (:Person:Human {login: r.login, bot: false})",
        [{"login": p} for p in people if not is_bot(p)])
    run("UNWIND $rows AS r CREATE (:Person:Bot {login: r.login, bot: true})",
        [{"login": p} for p in people if is_bot(p)])
    # CODEOWNERS owners that never showed up in activity are still part of the blueprint
    run("UNWIND $rows AS r MERGE (t:Team {slug: r.slug})",
        [{"slug": o} for o in owner_names if "/" in o])
    run("UNWIND $rows AS r MERGE (p:Person {login: r.login}) ON CREATE SET p.bot = false, p:Human",
        [{"login": o} for o in owner_names if "/" not in o and "@" not in o])
    run("UNWIND $rows AS r CREATE (:PR {number: r.number, title: r.title, url: r.url, created: r.created,"
        " merged: r.merged, additions: r.additions, deletions: r.deletions})", prs)
    run("UNWIND $rows AS r CREATE (:Issue {number: r.number, title: r.title, url: r.url, created: r.created})",
        list(issues.values()))
    run("UNWIND $rows AS r CREATE (:Area {path: r.path, depth: r.depth, name: r.name})", area_rows)
    run("UNWIND $rows AS r MATCH (c:Area {path: r.child}), (p:Area {path: r.parent}) CREATE (c)-[:IN]->(p)",
        area_parent)
    run("UNWIND $rows AS r MATCH (a:Area {path: r.area}) CREATE (:File {path: r.path})-[:IN]->(a)", file_rows)
    run("UNWIND $rows AS r MATCH (p:Person {login: r.login}), (x:PR {number: r.pr}) CREATE (p)-[:AUTHORED]->(x)",
        authored)
    run("UNWIND $rows AS r MATCH (p:Person {login: r.login}), (x:PR {number: r.pr}) CREATE (p)-[:MERGED]->(x)",
        merged)
    run("UNWIND $rows AS r MATCH (p:Person {login: r.login}), (x:PR {number: r.pr})"
        " CREATE (p)-[:REVIEWED {state: r.state, at: r.at}]->(x)", reviewed)
    run("UNWIND $rows AS r MATCH (x:PR {number: r.pr}), (f:File {path: r.path})"
        " CREATE (x)-[:TOUCHED {additions: r.add, deletions: r.del}]->(f)", touched)
    run("UNWIND $rows AS r MATCH (x:PR {number: r.pr}), (i:Issue {number: r.issue}) CREATE (x)-[:CLOSES]->(i)",
        closes)
    run("UNWIND $rows AS r MATCH (p:Person {login: r.login}), (i:Issue {number: r.issue})"
        " CREATE (p)-[:OPENED]->(i)", opened)
    run("UNWIND $rows AS r MATCH (o) WHERE (o:Team AND o.slug = r.owner) OR (o:Person AND o.login = r.owner)"
        " MATCH (f:File {path: r.path}) CREATE (o)-[:OWNS]->(f)", owns)

    derive(g)
    g.query("CREATE INDEX FOR (o:OpenPR) ON (o.number)")
    for pr in snapshot.get("open_prs", []):
        upsert_open_pr(g, pr)
    return stats(g)


def upsert_open_pr(g, pr: dict) -> None:
    """Add (or refresh) an open PR: who opened it, who is asked to review, which areas it touches.

    Each area gets a weight: the share of changed lines in it, scaled by depth so that
    specific areas (src/execution_plan/ops) count more than broad ones (src).
    """
    n = pr["number"]
    g.query("MATCH (o:OpenPR {number: $n}) DETACH DELETE o", {"n": n})
    g.query("""
        CREATE (:OpenPR {number: $n, title: $title, url: $url, created: $created, updated: $updated,
                         draft: $draft, additions: $add, deletions: $del})
    """, {"n": n, "title": pr["title"], "url": pr["url"], "created": epoch(pr["createdAt"]),
          "updated": epoch(pr["updatedAt"]), "draft": bool(pr.get("isDraft")),
          "add": pr.get("additions") or 0, "del": pr.get("deletions") or 0})
    if pr.get("author"):
        login = pr["author"]["login"]
        g.query("MERGE (p:Person {login: $l}) ON CREATE SET p.bot = $bot", {"l": login, "bot": is_bot(login)})
        g.query("MATCH (p:Person {login: $l}), (o:OpenPR {number: $n}) CREATE (p)-[:OPENED_PR]->(o)",
                {"l": login, "n": n})
    for rr in pr["reviewRequests"]["nodes"]:
        who = rr["requestedReviewer"] or {}
        if who.get("login"):
            g.query("MERGE (p:Person {login: $l}) ON CREATE SET p.bot = $bot", {"l": who["login"], "bot": is_bot(who["login"])})
            g.query("MATCH (p:Person {login: $l}), (o:OpenPR {number: $n}) CREATE (o)-[:REQUESTS]->(p)",
                    {"l": who["login"], "n": n})
        elif who.get("combinedSlug"):
            g.query("MERGE (t:Team {slug: $s})", {"s": who["combinedSlug"]})
            g.query("MATCH (t:Team {slug: $s}), (o:OpenPR {number: $n}) CREATE (o)-[:REQUESTS]->(t)",
                    {"s": who["combinedSlug"], "n": n})
    for rv in pr["reviews"]["nodes"]:
        if rv["author"]:
            g.query("MATCH (p:Person {login: $l}), (o:OpenPR {number: $n}) "
                    "CREATE (p)-[:REVIEWED_OPEN {state: $s, at: $at}]->(o)",
                    {"l": rv["author"]["login"], "n": n, "s": rv["state"], "at": epoch(rv["submittedAt"])})
    # Changed lines per area, mapped onto the deepest area that already exists in the graph.
    existing = {r[0] for r in g.ro_query("MATCH (a:Area) RETURN a.path").result_set}
    lines: dict[str, float] = {}
    for f in pr["files"]["nodes"]:
        size = (f.get("additions") or 0) + (f.get("deletions") or 0) + 1
        chain = [a for a in areas_of(f["path"]) if a in existing]
        for a in chain:
            depth = 0 if a == "/" else a.count("/") + 1
            lines[a] = lines.get(a, 0) + size * depth / AREA_DEPTH
    total = sum(lines.values()) or 1
    g.query("""
        UNWIND $rows AS r MATCH (o:OpenPR {number: $n}), (a:Area {path: r.a})
        CREATE (o)-[:TOUCHES {weight: r.w}]->(a)
    """, {"n": n, "rows": [{"a": a, "w": w / total} for a, w in lines.items() if w > 0]})


def derive(g, now: int | None = None) -> None:
    """Compute desire paths and blueprint roll-ups with Cypher."""
    now = now or int(time.time())
    tau = HALF_LIFE_DAYS * 86400 / 0.6931

    # Each PR belongs to every area (and ancestor area) of the files it touched.
    g.query("""
        MATCH (x:PR)-[:TOUCHED]->(:File)-[:IN]->(leaf:Area)
        WITH DISTINCT x, leaf
        MATCH p = (leaf)-[:IN*0..]->(a:Area)
        WITH DISTINCT x, a
        CREATE (x)-[:IN_AREA]->(a)
    """)

    # Desire paths: authorship counts most, approvals and change requests next, comments least.
    g.query("""
        MATCH (p:Person {bot: false})-[r:AUTHORED|REVIEWED|MERGED]->(x:PR)-[:IN_AREA]->(a:Area)
        WITH p, a, x,
             max(CASE type(r)
                   WHEN 'AUTHORED' THEN 1.0
                   WHEN 'REVIEWED' THEN CASE r.state WHEN 'APPROVED' THEN 0.6
                                                     WHEN 'CHANGES_REQUESTED' THEN 0.7
                                                     ELSE 0.35 END
                   ELSE 0.2 END) AS w,
             max(CASE type(r) WHEN 'AUTHORED' THEN 1 ELSE 0 END) AS isAuthor
        WITH p, a,
             sum(w * exp(-1.0 * ($now - x.merged) / $tau)) AS score,
             count(x) AS prs, sum(isAuthor) AS authored, max(x.merged) AS last
        CREATE (p)-[:WORN {score: score, prs: prs, authored: authored, reviewed: prs - authored, last: last}]->(a)
    """, {"now": now, "tau": tau})

    # Collaboration: reviewer -> author, decayed the same way.
    g.query("""
        MATCH (rv:Person {bot: false})-[r:REVIEWED]->(x:PR)<-[:AUTHORED]-(au:Person {bot: false})
        WHERE rv <> au
        WITH rv, au, x
        WITH rv, au, count(DISTINCT x) AS n, max(x.merged) AS last,
             sum(exp(-1.0 * ($now - x.merged) / $tau)) AS score
        CREATE (rv)-[:COLLAB {n: n, last: last, score: score}]->(au)
    """, {"now": now, "tau": tau})

    g.query("""
        MATCH (:Human)-[w:WORN]->(a:Area) WITH a, sum(w.score) AS t SET a.wear = t
    """)

    # Area sizes and activity totals, used for coverage and for sizing nodes in the UI.
    g.query("""
        MATCH (f:File)-[:IN]->(leaf:Area)
        WITH leaf, count(f) AS c
        MATCH (leaf)-[:IN*0..]->(a:Area)
        WITH a, sum(c) AS files
        SET a.files = files
    """)
    g.query("""
        MATCH (x:PR)-[:IN_AREA]->(a:Area)
        WITH a, count(x) AS prs, max(x.merged) AS last
        SET a.prs = prs, a.last = last
    """)

    # Blueprint roll-up: an owner is official for an area if they own any file in it.
    g.query("""
        MATCH (o)-[:OWNS]->(f:File)-[:IN]->(leaf:Area)
        WITH o, leaf, count(f) AS c
        MATCH (leaf)-[:IN*0..]->(a:Area)
        WITH o, a, sum(c) AS owned
        CREATE (o)-[:OFFICIAL {coverage: toFloat(owned) / a.files}]->(a)
    """)


def stats(g) -> dict:
    out = {}
    for label in ["Person", "Team", "PR", "OpenPR", "Issue", "File", "Area"]:
        out[label] = g.ro_query(f"MATCH (n:{label}) RETURN count(n)").result_set[0][0]
    for rel in ["WORN", "COLLAB", "OFFICIAL", "IN_AREA"]:
        out[rel] = g.ro_query(f"MATCH ()-[r:{rel}]->() RETURN count(r)").result_set[0][0]
    return out


if __name__ == "__main__":
    import json
    import sys

    from desire_paths.github import DATA_DIR, load_snapshot

    # No arguments: rebuild every cached snapshot in data/ (used at container start).
    repos = sys.argv[1:] or [json.loads(p.read_text())["repo"] for p in sorted(DATA_DIR.glob("*.json"))]
    for repo in repos:
        t = time.time()
        print(repo, build(load_snapshot(repo)), f"{time.time() - t:.1f}s", flush=True)
