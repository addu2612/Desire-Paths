"""Graph tools the agent can call.

Every tool returns a dict with:
  data       - facts for the LLM to reason over
  evidence   - PRs/issues that back the facts (cited in the answer)
  highlight  - node ids and links for the UI to light up ("p:login", "a:path", "t:slug")
"""

from __future__ import annotations

import re
import time

DAY = 86400


def pid(login: str) -> str:
    return f"p:{login}"


def aid(path: str) -> str:
    return f"a:{path}"


def oid(owner_label: str, name: str) -> str:
    return f"t:{name}" if owner_label == "Team" else f"p:{name}"


def ago(ts: int | None) -> str:
    if not ts:
        return "never"
    days = int((time.time() - ts) / DAY)
    if days < 1:
        return "today"
    if days < 60:
        return f"{days}d ago"
    return f"{days // 30}mo ago"


def _empty() -> dict:
    return {"nodes": [], "links": []}


def activity_factor(last: int | None, now: float | None = None) -> float:
    if not last:
        return 0.05
    days = ((now or time.time()) - last) / DAY
    return 1.0 if days < 30 else 0.7 if days < 90 else 0.3 if days < 180 else 0.1


class GraphTools:
    def __init__(self, graph, repo: str | None = None):
        self.g = graph
        self.repo = repo
        # Every Cypher query a tool runs, so the UI can show the graph proof behind an answer.
        self.trace: list[dict] = []

    # ---------- helpers ----------

    def _q(self, query: str, params: dict | None = None) -> list[list]:
        t = time.perf_counter()
        rows = self.g.ro_query(query, params or {}).result_set
        self.trace.append({"cypher": _tidy(query), "params": _short(params or {}), "rows": len(rows),
                           "ms": round((time.perf_counter() - t) * 1000, 1)})
        return rows

    def run(self, name: str, *args, **kwargs) -> dict:
        """Call a tool and attach the Cypher it ran."""
        self.trace = []
        result = getattr(self, name)(*args, **kwargs)
        # The same query run once per PR is one step of proof, not forty.
        proof: dict[str, dict] = {}
        for step in self.trace:
            seen = proof.get(step["cypher"])
            if seen:
                seen["runs"] += 1
                seen["rows"] += step["rows"]
                seen["ms"] = round((seen["ms"] or 0) + (step["ms"] or 0), 1)
            else:
                proof[step["cypher"]] = {**step, "runs": 1}
        result["proof"] = list(proof.values())[:14]
        return result

    def resolve_area(self, query: str) -> str | None:
        q = query.strip().strip("/").lower()
        if q in ("", "root", "(root)", "everything", "repo"):
            return "/"
        rows = self._q("""
            MATCH (a:Area) WHERE toLower(a.path) = $q OR toLower(a.name) = $q
               OR toLower(a.path) ENDS WITH ('/' + $q) OR toLower(a.path) CONTAINS $q
            RETURN a.path, a.prs, toLower(a.path) = $q OR toLower(a.name) = $q AS exact
            ORDER BY exact DESC, a.prs DESC LIMIT 1
        """, {"q": q})
        return rows[0][0] if rows else None

    def _official(self, area: str) -> list[dict]:
        rows = self._q("""
            MATCH (o)-[r:OFFICIAL]->(:Area {path: $a})
            RETURN labels(o)[0], coalesce(o.slug, o.login), r.coverage ORDER BY r.coverage DESC
        """, {"a": area})
        return [{"owner": n, "kind": lbl, "coverage": round(c, 2)} for lbl, n, c in rows]

    def _evidence_for(self, login: str, area: str, limit: int = 3) -> list[dict]:
        rows = self._q("""
            MATCH (p:Person {login: $login})-[r:AUTHORED|REVIEWED]->(x:PR)-[:IN_AREA]->(:Area {path: $a})
            RETURN DISTINCT x.number, x.title, x.url, x.merged, type(r) ORDER BY x.merged DESC LIMIT $n
        """, {"login": login, "a": area, "n": limit})
        return [{"kind": "PR", "number": n, "title": t, "url": u, "when": ago(m),
                 "role": "authored" if rt == "AUTHORED" else "reviewed", "by": login}
                for n, t, u, m, rt in rows]

    # ---------- tools ----------

    def find_areas(self, query: str) -> dict:
        rows = self._q("""
            MATCH (a:Area) WHERE toLower(a.path) CONTAINS toLower($q)
            RETURN a.path, a.prs, a.files ORDER BY a.prs DESC LIMIT 12
        """, {"q": query})
        return {"data": [{"area": p, "prs": n, "files": f} for p, n, f in rows], "evidence": [],
                "highlight": {"nodes": [aid(p) for p, _, _ in rows], "links": []}}

    def who_knows(self, area: str) -> dict:
        """Official owners vs. the people who actually worked on an area recently."""
        path = self.resolve_area(area)
        if not path:
            return self.who_knows_topic(area)
        rows = self._q("""
            MATCH (p:Human)-[w:WORN]->(a:Area {path: $a})
            RETURN p.login, w.score, w.prs, w.authored, w.reviewed, w.last
            ORDER BY w.score DESC LIMIT 6
        """, {"a": path})
        total = sum(r[1] for r in rows) or 1
        people = [{"login": login, "share_of_recent_work": round(s / total, 2), "prs": n, "authored": au,
                   "reviewed": rv, "last_active": ago(last)} for login, s, n, au, rv, last in rows]
        official = self._official(path)
        # Is each official owner actually active here?
        for o in official:
            act = self._q("""
                MATCH (p:Person {login: $o})-[w:WORN]->(:Area {path: $a}) RETURN w.prs, w.last
            """, {"o": o["owner"], "a": path}) if o["kind"] != "Team" else []
            o["active_here"] = {"prs": act[0][0], "last": ago(act[0][1])} if act else None
        evidence = [e for p in people[:2] for e in self._evidence_for(p["login"], path, 3)]
        links = [[pid(p["login"]), aid(path), "worn"] for p in people[:3]]
        links += [[oid(o["kind"], o["owner"]), aid(path), "official"] for o in official[:3]]
        return {
            "data": {"area": path, "official_owners": official or "none - no CODEOWNERS rule covers this area",
                     "actual_people": people},
            "evidence": evidence,
            "highlight": {"nodes": [aid(path)] + [link[0] for link in links], "links": links},
        }

    def who_knows_topic(self, topic: str) -> dict:
        """Full-text search over PR and issue titles, then follow the graph to the people involved."""
        terms = " ".join(w for w in re.findall(r"[A-Za-z0-9_]+", topic) if len(w) > 2)
        if not terms:
            return {"data": "no searchable terms", "evidence": [], "highlight": _empty()}
        try:
            rows = self._q("""
                CALL db.idx.fulltext.queryNodes('PR', $q) YIELD node, score
                WITH node AS x, score ORDER BY score DESC LIMIT 25
                MATCH (p:Human)-[r:AUTHORED|REVIEWED]->(x)
                WITH p, x, max(CASE type(r) WHEN 'AUTHORED' THEN 1.0 ELSE 0.5 END) AS w
                WITH p, sum(w) AS weight, collect({n: x.number, t: x.title, u: x.url, m: x.merged}) AS prs
                RETURN p.login, weight, prs ORDER BY weight DESC LIMIT 5
            """, {"q": terms})
        except Exception as e:  # malformed full-text syntax
            return {"data": f"search failed: {e}", "evidence": [], "highlight": _empty()}
        people, evidence = [], []
        for login, weight, prs in rows:
            prs = sorted(prs, key=lambda x: -(x["m"] or 0))
            people.append({"login": login, "weight": weight, "matching_prs": len(prs), "last": ago(prs[0]["m"])})
            evidence += [{"kind": "PR", "number": x["n"], "title": x["t"], "url": x["u"], "when": ago(x["m"]),
                          "by": login} for x in prs[:2]]
        areas = self._q("""
            CALL db.idx.fulltext.queryNodes('PR', $q) YIELD node
            MATCH (node)-[:IN_AREA]->(a:Area) WHERE a.depth >= 2
            RETURN a.path, count(*) AS c ORDER BY c DESC LIMIT 3
        """, {"q": terms})
        nodes = [pid(p["login"]) for p in people] + [aid(a) for a, _ in areas]
        links = [[pid(p["login"]), aid(a), "worn"] for p in people[:3] for a, _ in areas[:1]]
        return {"data": {"topic": topic, "people": people, "related_areas": [a for a, _ in areas]},
                "evidence": evidence[:6], "highlight": {"nodes": nodes, "links": links}}

    def bus_factor(self, min_prs: int = 8) -> dict:
        """Areas where one person did most of the recent work."""
        rows = self._q("""
            MATCH (p:Human)-[w:WORN]->(a:Area) WHERE a.depth >= 1 AND a.depth <= 3 AND a.prs >= $min
            WITH a, w, p ORDER BY w.score DESC
            WITH a, collect({login: p.login, score: w.score}) AS ppl, sum(w.score) AS total
            WITH a, ppl[0] AS top, total, size(ppl) AS n
            WHERE total > 0 AND top.score / total >= 0.6
            OPTIONAL MATCH (o)-[:OFFICIAL]->(a)
            RETURN a.path, a.prs, top.login, top.score / total AS share, n, collect(coalesce(o.slug, o.login))
            ORDER BY a.prs * (top.score / total) DESC LIMIT 10
        """, {"min": min_prs})
        data = [{"area": a, "prs": prs, "single_point": top, "share_of_recent_work": round(sh, 2),
                 "people_involved": n, "official_owners": off or "none"} for a, prs, top, sh, n, off in rows]
        return {"data": data, "evidence": [],
                "highlight": {"nodes": [aid(d["area"]) for d in data] + [pid(d["single_point"]) for d in data],
                              "links": [[pid(d["single_point"]), aid(d["area"]), "risk"] for d in data]}}

    def hidden_bottlenecks(self) -> dict:
        """Betweenness centrality on the review graph: who connects otherwise separate people."""
        rows = self._q("""
            CALL algo.betweenness({nodeLabels: ['Human'], relationshipTypes: ['COLLAB']})
            YIELD node, score WHERE score > 0
            WITH node, score ORDER BY score DESC LIMIT 6
            OPTIONAL MATCH (node)-[:OFFICIAL]->(a:Area)
            WITH node, score, count(a) AS owns
            OPTIONAL MATCH (node)-[c:COLLAB]-(other:Human)
            RETURN node.login, score, owns, count(DISTINCT other) AS partners
            ORDER BY score DESC
        """)
        data = [{"login": login, "betweenness": round(s, 1), "official_areas": o, "review_partners": n}
                for login, s, o, n in rows]
        return {"data": data, "evidence": [],
                "highlight": {"nodes": [pid(d["login"]) for d in data], "links": []}}

    def influence(self) -> dict:
        """PageRank on reviewer->author edges: whose code review the most-reviewed people rely on."""
        rows = self._q("""
            CALL algo.pageRank('Human', 'COLLAB') YIELD node, score
            RETURN node.login, score ORDER BY score DESC LIMIT 8
        """)
        return {"data": [{"login": login, "pagerank": round(s, 5)} for login, s in rows], "evidence": [],
                "highlight": {"nodes": [pid(login) for login, _ in rows], "links": []}}

    def silos(self) -> dict:
        """Community detection on the review graph, with each group's home areas."""
        rows = self._q("""
            CALL algo.labelPropagation({nodeLabels: ['Human'], relationshipTypes: ['COLLAB'], maxIterations: 20})
            YIELD node, communityId
            WITH communityId, collect(node) AS members WHERE size(members) >= 2
            UNWIND members AS m
            OPTIONAL MATCH (m)-[w:WORN]->(a:Area) WHERE a.depth = 2
            WITH communityId, members, a, sum(w.score) AS s ORDER BY s DESC
            WITH communityId, members, collect(a.path)[0..3] AS areas
            RETURN communityId, [m IN members | m.login], areas ORDER BY size(members) DESC LIMIT 6
        """)
        groups = [{"group": cid, "members": m, "home_areas": a} for cid, m, a in rows]
        # How many review links cross between each pair of groups?
        index = {login: g["group"] for g in groups for login in g["members"]}
        cross = self._q("""
            MATCH (a:Human)-[c:COLLAB]->(b:Human) WHERE a.login IN $logins AND b.login IN $logins
            RETURN a.login, b.login, c.n
        """, {"logins": list(index)})
        between: dict[str, int] = {}
        for a, b, n in cross:
            ga, gb = index[a], index[b]
            if ga != gb:
                key = "-".join(map(str, sorted((ga, gb))))
                between[key] = between.get(key, 0) + n
        return {"data": {"groups": groups,
                         "reviews_between_groups": between or "none - groups never review each other"},
                "evidence": [], "highlight": {"nodes": [pid(login) for login in index], "links": []}}

    def intro_path(self, from_login: str, to_login: str) -> dict:
        """Shortest chain of review relationships between two people."""
        for a, b in ((from_login, to_login), (to_login, from_login)):
            rows = self._q("""
                MATCH (s:Person {login: $a}), (t:Person {login: $b})
                CALL algo.SPpaths({sourceNode: s, targetNode: t, relTypes: ['COLLAB'], maxLen: 6, pathCount: 1})
                YIELD path RETURN [n IN nodes(path) | n.login]
            """, {"a": a, "b": b})
            if rows:
                chain = rows[0][0] if a == from_login else list(reversed(rows[0][0]))
                links = [[pid(x), pid(y), "path"] for x, y in zip(chain, chain[1:], strict=False)]
                return {"data": {"chain": chain, "hops": len(chain) - 1}, "evidence": [],
                        "highlight": {"nodes": [pid(x) for x in chain], "links": links}}
        return {"data": f"no review chain connects {from_login} and {to_login}", "evidence": [],
                "highlight": {"nodes": [pid(from_login), pid(to_login)], "links": []}}

    def person(self, login: str) -> dict:
        rows = self._q("""
            MATCH (p:Person {login: $l})-[w:WORN]->(a:Area) WHERE a.depth >= 2
            RETURN a.path, w.prs, w.authored, w.last, w.score ORDER BY w.score DESC LIMIT 6
        """, {"l": login})
        partners = self._q("""
            MATCH (p:Person {login: $l})-[c:COLLAB]-(o:Human)
            RETURN o.login, sum(c.n) AS n ORDER BY n DESC LIMIT 5
        """, {"l": login})
        official = self._q("""
            MATCH (p:Person {login: $l})-[r:OFFICIAL]->(a:Area) WHERE a.depth <= 2 RETURN a.path LIMIT 10
        """, {"l": login})
        return {"data": {"login": login,
                         "works_on": [{"area": a, "prs": n, "authored": au, "last": ago(la)}
                                      for a, n, au, la, _ in rows],
                         "reviews_with": [{"login": o, "reviews": n} for o, n in partners],
                         "officially_owns": [a for (a,) in official] or "nothing in CODEOWNERS"},
                "evidence": [],
                "highlight": {"nodes": [pid(login)] + [aid(r[0]) for r in rows[:4]],
                              "links": [[pid(login), aid(r[0]), "worn"] for r in rows[:4]]}}

    def blueprint_drift(self) -> dict:
        """Where CODEOWNERS and reality disagree: busy areas with no owner, and owners who are absent."""
        unowned = self._q("""
            MATCH (a:Area) WHERE a.depth >= 1 AND a.depth <= 2 AND a.prs >= 5 AND NOT ()-[:OFFICIAL]->(a)
            OPTIONAL MATCH (p:Human)-[w:WORN]->(a)
            WITH a, p, w ORDER BY w.score DESC
            WITH a, collect(p.login)[0..2] AS actual
            RETURN a.path, a.prs, actual ORDER BY a.prs DESC LIMIT 8
        """)
        ghosts = self._q("""
            MATCH (o:Person)-[r:OFFICIAL]->(a:Area) WHERE a.depth >= 1 AND a.depth <= 3 AND a.prs >= 5
              AND r.coverage >= 0.5
            OPTIONAL MATCH (o)-[w:WORN]->(a)
            WITH o, a, w WHERE w IS NULL OR w.last < $cutoff
            OPTIONAL MATCH (p:Human)-[w2:WORN]->(a)
            WITH o, a, w, p, w2 ORDER BY w2.score DESC
            WITH o, a, w, collect(p.login)[0] AS actual
            RETURN o.login, a.path, a.prs, w.last, actual ORDER BY a.prs DESC LIMIT 8
        """, {"cutoff": int(time.time()) - 120 * DAY})
        team_owned = self._q("""
            MATCH (t:Team)-[r:OFFICIAL]->(a:Area) WHERE a.depth = 1 RETURN t.slug, a.path, r.coverage LIMIT 6
        """)
        cov = self._q("MATCH (r:Area {path: '/'}) OPTIONAL MATCH ()-[o:OFFICIAL]->(r) RETURN max(o.coverage)")
        data = {
            "share_of_files_with_an_official_owner": round(cov[0][0] or 0, 2),
            "busy_areas_with_no_official_owner": [{"area": a, "prs": n, "actually_worked_on_by": p}
                                                  for a, n, p in unowned],
            "official_owners_absent_for_4_months": [{"owner": o, "area": a, "prs_in_area": n,
                                                     "owner_last_active_here": ago(seen), "actually_led_by": act}
                                                    for o, a, n, seen, act in ghosts],
            "team_ownership": [{"team": t, "area": a, "coverage": round(c, 2)} for t, a, c in team_owned],
        }
        nodes = [aid(a) for a, _, _ in unowned] + [aid(g[1]) for g in ghosts] + [pid(g[0]) for g in ghosts]
        links = [[pid(p[0]), aid(a), "worn"] for a, _, p in unowned if p]
        links += [[pid(o), aid(a), "official"] for o, a, *_ in ghosts]
        return {"data": data, "evidence": [], "highlight": {"nodes": nodes, "links": links}}

    def pave(self, min_share: float = 0.25) -> dict:
        """Draft CODEOWNERS lines from desire paths: pave where people actually walk.

        Paving only adds. Areas an existing rule already covers completely are left alone (the existing
        rules are kept and come last, so they would win anyway), and a folder that would get the same
        owners as its parent needs no line of its own.
        """
        rows = self._q("""
            MATCH (p:Human)-[w:WORN]->(a:Area) WHERE a.depth >= 1 AND a.depth <= 2 AND a.prs >= 4
            WITH a, p, w ORDER BY w.score DESC
            WITH a, collect({login: p.login, score: w.score, authored: w.authored}) AS ppl, sum(w.score) AS total
            OPTIONAL MATCH (o)-[off:OFFICIAL]->(a)
            RETURN a.path, ppl[0..3], total, collect(coalesce(o.slug, o.login)), a.prs, max(off.coverage)
            ORDER BY a.prs DESC
        """)
        picked, changes = {}, []
        for path, ppl, total, official, _, covered in rows:
            if (covered or 0) >= 0.999:
                continue
            picks = [p["login"] for p in ppl if total and p["score"] / total >= min_share and p["authored"] > 0][:2]
            if picks:
                picked[path] = (picks, official)
        lines = []
        for path in sorted(picked):
            picks, official = picked[path]
            parent = path.rsplit("/", 1)[0] if "/" in path else None
            if parent in picked and set(picked[parent][0]) == set(picks):
                continue
            lines.append(f"/{path}/".ljust(40) + " ".join("@" + p for p in picks))
            if set(picks) != set(official):
                changes.append({"area": path, "today": official or "nobody", "suggested": picks})
        changes.sort(key=lambda c: c["today"] != "nobody")
        return {"data": {"codeowners": "\n".join(lines), "changes_vs_today": changes[:15],
                         "note": "Existing CODEOWNERS rules are kept unchanged and placed last, so they still win."},
                "evidence": [],
                "highlight": {"nodes": [aid(c["area"]) for c in changes], "links":
                              [[pid(p), aid(c["area"]), "worn"] for c in changes for p in c["suggested"]]}}

    # ---------- decisions on open PRs ----------

    def _candidates(self, number: int, limit: int = 12, extra_load: dict | None = None,
                    exclude: list[str] | None = None) -> list[dict]:
        """Multi-hop: open PR -> areas it touches -> people who walk those areas -> their review load."""
        rows = self._q("""
            MATCH (o:OpenPR {number: $n})-[t:TOUCHES]->(a:Area)<-[w:WORN]-(p:Human)
            WHERE NOT (p)-[:OPENED_PR]->(o) AND a.wear > 0 AND NOT p.login IN $ex
            WITH o, p, sum(t.weight * w.score / a.wear) AS fit, max(w.last) AS last,
                 collect({area: a.path, share: w.score / a.wear, prs: w.prs, tw: t.weight}) AS via
            OPTIONAL MATCH (p)<-[:REQUESTS]-(q:OpenPR) WHERE q.number <> $n AND NOT q.draft
            WITH o, p, fit, last, via, count(DISTINCT q) AS load
            OPTIONAL MATCH (p)-[c:COLLAB]->(:Person)-[:OPENED_PR]->(o)
            RETURN p.login, fit, last, via, load, coalesce(max(c.n), 0)
            ORDER BY fit DESC LIMIT $k
        """, {"n": number, "k": limit, "ex": exclude or []})
        out = []
        for login, fit, last, via, load, familiar in rows:
            load += (extra_load or {}).get(login, 0)
            via = sorted(via, key=lambda v: -(v["tw"] * v["share"]))
            score = fit * activity_factor(last) / (1 + 0.12 * load) * (1 + 0.06 * min(familiar, 5))
            out.append({"login": login, "fit": fit, "score": score, "last": last, "load": load,
                        "reviewed_author_before": familiar, "via": via[:3]})
        return sorted(out, key=lambda c: -c["score"])

    def _ensure_pr(self, number: int, live: bool) -> dict | None:
        from desire_paths.github import fetch_pr
        from desire_paths.graph import upsert_open_pr

        have = self._q("MATCH (o:OpenPR {number: $n}) RETURN o.number", {"n": number})
        if (live or not have) and self.repo:
            try:
                pr = fetch_pr(self.repo, number)
            except Exception:
                pr = None
            if pr and pr.get("state") == "OPEN":
                upsert_open_pr(self.g, pr)
            elif pr and not have:
                return {"error": f"PR #{number} is {pr.get('state', 'not open').lower()}"}
        rows = self._q("""
            MATCH (o:OpenPR {number: $n})
            OPTIONAL MATCH (au:Person)-[:OPENED_PR]->(o)
            OPTIONAL MATCH (o)-[:REQUESTS]->(r)
            OPTIONAL MATCH (h:Human)-[:REVIEWED_OPEN]->(o)
            RETURN o.title, o.url, o.created, o.draft, au.login,
                   collect(DISTINCT coalesce(r.login, r.slug)), collect(DISTINCT h.login)
        """, {"n": number})
        if not rows:
            return None
        t, u, c, d, au, req, rev = rows[0]
        return {"number": number, "title": t, "url": u, "opened": ago(c), "draft": d, "author": au,
                "requested": [x for x in req if x], "reviewed_by": [x for x in rev if x]}

    def route_pr(self, number: int | str, live: bool = True) -> dict:
        """Decide who should review an open PR, and explain every step of the decision."""
        number = int(str(number).lstrip("#"))
        pr = self._ensure_pr(number, live)
        if not pr or pr.get("error"):
            return {"data": (pr or {}).get("error") or f"PR #{number} not found", "evidence": [],
                    "highlight": _empty()}
        areas = self._q("""
            MATCH (:OpenPR {number: $n})-[t:TOUCHES]->(a:Area) WHERE a.depth >= 1
            RETURN a.path, t.weight ORDER BY t.weight DESC LIMIT 5
        """, {"n": number})
        cands = self._candidates(number)
        if not cands:
            return {"data": {"pr": pr, "touches": [a for a, _ in areas],
                             "decision": "nobody has walked these areas recently - needs a maintainer's call"},
                    "evidence": [], "highlight": {"nodes": [aid(a) for a, _ in areas], "links": []}}
        primary = cands[0]
        # Second reviewer: spread knowledge where the primary is a single point of failure.
        top_area = primary["via"][0]
        second, why2 = None, None
        if top_area["share"] >= 0.5:
            pool = [c for c in cands[1:] if any(v["area"] == top_area["area"] for v in c["via"])
                    and activity_factor(c["last"]) >= 0.7]
            if pool:
                second = pool[0]
                why2 = (f"knowledge spreading: @{primary['login']} does {round(top_area['share'] * 100)}% of the "
                        f"recent work in {top_area['area']}, so @{second['login']} learns it too")
        if not second and len(cands) > 1:
            second = cands[1]
            why2 = "next-best fit across the touched areas"

        def explain(c):
            v = c["via"][0]
            return {"login": c["login"], "fit": round(c["fit"], 3), "score": round(c["score"], 3),
                    "main_area": v["area"], "share_of_recent_work_there": round(v["share"], 2),
                    "prs_there": v["prs"], "last_active": ago(c["last"]), "open_review_requests": c["load"],
                    "has_reviewed_author_before": c["reviewed_author_before"]}

        rejected = []
        for c in sorted(cands, key=lambda c: -c["fit"])[:6]:
            if c["login"] in (primary["login"], second and second["login"]):
                continue
            reasons = []
            if activity_factor(c["last"]) < 0.7:
                reasons.append(f"inactive (last seen {ago(c['last'])})")
            if c["load"] >= 5:
                reasons.append(f"overloaded ({c['load']} open review requests)")
            if reasons:
                rejected.append({"login": c["login"], "fit": round(c["fit"], 3), "why_not": ", ".join(reasons)})
        verdicts = []
        by_login = {c["login"]: c for c in cands}
        for r in pr["requested"]:
            c = by_login.get(r)
            if "/" in r:
                verdicts.append({"requested": r, "verdict": "team request - nobody specific is accountable"})
            elif not c:
                verdicts.append({"requested": r, "verdict": "no recent work in the areas this PR touches"})
            elif activity_factor(c["last"]) < 0.7:
                verdicts.append({"requested": r,
                                 "verdict": f"knows the area but inactive (last seen {ago(c['last'])})"})
            else:
                verdicts.append({"requested": r, "verdict": "good choice"})
        evidence = self._evidence_for(primary["login"], top_area["area"], 3)
        if second:
            evidence += self._evidence_for(second["login"], second["via"][0]["area"], 2)
        picks = [primary] + ([second] if second else [])
        acted = self._q("""
            MATCH (x:Action)-[:FOR]->(:OpenPR {number: $n}) RETURN x.kind, x.status, x.at, x.url ORDER BY x.at DESC
        """, {"n": number})
        data = {
            "pr": pr,
            "touches": [{"area": a, "share_of_change": round(w, 2)} for a, w in areas],
            "decision": {"primary": explain(primary), "second": explain(second) if second else None,
                         "why_second": why2},
            "currently_requested": verdicts or "nobody requested yet",
            "considered_and_rejected": rejected,
            "past_actions_on_this_pr": [{"kind": k, "status": st, "when": ago(at), "url": u} for k, st, at, u in acted],
            "proposed_action": {"kind": "request_reviewers", "number": number,
                                "reviewers": [c["login"] for c in picks]},
        }
        nodes = [aid(a) for a, _ in areas] + [pid(c["login"]) for c in picks]
        links = [[pid(c["login"]), aid(c["via"][0]["area"]), "path"] for c in picks]
        links += [[pid(c["login"]), aid(v["area"]), "worn"] for c in picks for v in c["via"][1:]]
        return {"data": data, "evidence": evidence, "highlight": {"nodes": nodes, "links": links}}

    def triage(self, limit: int = 40) -> dict:
        """Sweep the open review queue and flag PRs whose routing is broken."""
        prs = self._q("""
            MATCH (o:OpenPR) WHERE NOT o.draft AND o.updated > $since
            OPTIONAL MATCH (au:Person)-[:OPENED_PR]->(o)
            OPTIONAL MATCH (o)-[:REQUESTS]->(r)
            OPTIONAL MATCH (h:Human)-[:REVIEWED_OPEN]->(o) WHERE h <> au
            RETURN o.number, o.title, o.url, o.created, au.login, coalesce(au.bot, false),
                   collect(DISTINCT coalesce(r.login, r.slug)), count(DISTINCT h), o.updated AS updated
            ORDER BY updated DESC LIMIT $k
        """, {"since": int(time.time()) - 60 * DAY, "k": limit})
        flagged = []
        # Assign as we go: every suggestion adds to that reviewer's load for the next PR,
        # so the queue is spread across the team instead of piling onto the top two people.
        assigned: dict[str, int] = {}
        for n, title, url, created, author, author_bot, requested, human_reviews, _ in prs:
            requested = [r for r in requested if r]
            cands = self._candidates(n, 10, assigned)
            best = cands[0] if cands else None
            by_login = {c["login"]: c for c in cands}
            age = (time.time() - (created or time.time())) / DAY
            issues = []
            if not requested and human_reviews == 0 and age > 3:
                issues.append(("unrouted", 3, f"open {int(age)}d, nobody asked to review"))
            for r in requested:
                c = by_login.get(r)
                if "/" not in r and c and activity_factor(c["last"]) < 0.7:
                    issues.append(("absent-reviewer", 3, f"@{r} is requested but last active {ago(c['last'])}"))
                elif "/" not in r and not c:
                    issues.append(("wrong-reviewer", 2, f"@{r} has no recent work in these areas"))
            if human_reviews == 0 and age > 14:
                issues.append(("waiting", 1, f"{int(age)} days without a human review"))
            if not issues:
                continue
            flagged.append({
                "number": n, "title": title, "url": url, "author": author, "author_is_bot": author_bot,
                "requested": requested, "severity": max(s for _, s, _ in issues),
                "problems": [msg for _, _, msg in issues], "kinds": [k for k, _, _ in issues],
                "suggested": [c["login"] for c in cands[:2] if activity_factor(c["last"]) >= 0.7] if best else [],
            })
            for login in flagged[-1]["suggested"]:
                assigned[login] = assigned.get(login, 0) + 2
        flagged.sort(key=lambda f: (-f["severity"], f["number"]))
        summary = {}
        for f in flagged:
            for k in f["kinds"]:
                summary[k] = summary.get(k, 0) + 1
        return {"data": {"open_prs_checked": len(prs), "flagged": len(flagged), "by_problem": summary,
                         "suggested_assignments_per_reviewer": dict(sorted(assigned.items(), key=lambda kv: -kv[1])),
                         "queue": flagged[:15]},
                "evidence": [{"kind": "PR", "number": f["number"], "title": f["title"], "url": f["url"],
                              "when": "open"} for f in flagged[:6]],
                "highlight": {"nodes": [pid(x) for f in flagged[:10] for x in f["suggested"]], "links": []}}

    # ---------- what-ifs and early warnings ----------

    def if_gone(self, login: str) -> dict:
        """Remove one person from the graph and re-walk it: which PRs get stuck, which areas thin out,
        who reviews next, and whose code loses its main reviewer."""
        login = login.strip().lstrip("@")
        if not self._q("MATCH (p:Person {login: $l}) RETURN p.login", {"l": login}):
            return {"data": f"@{login} is not in the graph", "evidence": [], "highlight": _empty()}
        now = time.time()

        # Areas where they carry a real share of the recent work, and who is left once they are gone.
        rows = self._q("""
            MATCH (p:Human {login: $l})-[w:WORN]->(a:Area)
            WHERE a.depth >= 1 AND a.depth <= 3 AND a.prs >= 3 AND a.wear > 0 AND w.score / a.wear >= 0.2
            OPTIONAL MATCH (q:Human)-[w2:WORN]->(a) WHERE q.login <> $l
            WITH a, w, q, w2 ORDER BY w2.score DESC
            WITH a, w, collect({login: q.login, share: w2.score / a.wear, last: w2.last, prs: w2.prs}) AS rest
            RETURN a.path, a.depth, a.prs, w.score / a.wear AS share, w.prs, rest
            ORDER BY share * a.prs DESC LIMIT 40
        """, {"l": login})
        areas = []
        for path, depth, prs, share, their_prs, rest in rows:
            left = [r for r in rest if r["login"] and activity_factor(r["last"], now) >= 0.7]
            nxt = left[0] if left else None
            status = "orphaned" if not nxt else "thin" if nxt["share"] < 0.15 else "covered"
            areas.append({"area": path, "depth": depth, "prs": prs, "their_share": round(share, 2),
                          "their_prs": their_prs, "status": status,
                          "next_person": nxt and nxt["login"], "next_share": nxt and round(nxt["share"], 2)})
        # A sub-area that tells the same story as its parent adds nothing.
        seen = {(a["area"], a["status"], a["next_person"]) for a in areas}
        areas = [a for a in areas if "/" not in a["area"]
                 or (a["area"].rsplit("/", 1)[0], a["status"], a["next_person"]) not in seen]
        # Specific folders say more than "src" or "tests"; fall back to top level only if that is all there is.
        specific = [a for a in areas if a["depth"] >= 2]
        areas = specific if len(specific) >= 3 else areas
        order = {"orphaned": 0, "thin": 1, "covered": 2}
        areas.sort(key=lambda a: (order[a["status"]], -a["their_share"] * a["prs"]))

        # Open PRs that are waiting on them, or where they are the best reviewer the graph can find.
        queue = self._q("""
            MATCH (o:OpenPR) WHERE NOT o.draft AND o.updated > $since
            OPTIONAL MATCH (o)-[:REQUESTS]->(r:Person {login: $l})
            OPTIONAL MATCH (au:Person)-[:OPENED_PR]->(o)
            RETURN o.number, o.title, o.url, r IS NOT NULL, au.login, o.updated AS updated
            ORDER BY updated DESC LIMIT 60
        """, {"l": login, "since": int(now) - 60 * DAY})
        stuck, evidence = [], []
        for n, title, url, requested, author, _ in queue:
            if author == login:
                continue
            cands = self._candidates(n, 6)
            best = cands[0]["login"] if cands else None
            if not requested and best != login:
                continue
            nxt = next((c for c in self._candidates(n, 6, exclude=[login])
                        if activity_factor(c["last"], now) >= 0.7), None)
            mine = next((c for c in cands if c["login"] == login), None)
            ratio = round(nxt["fit"] / mine["fit"], 2) if nxt and mine and mine["fit"] else None
            stuck.append({
                "number": n, "title": title, "url": url,
                "why": "requested from them" if requested else "they are the best-fit reviewer",
                "next_reviewer": nxt and nxt["login"],
                "next_fit_vs_theirs": ratio,
                "backup": "none" if not nxt else "weak" if ratio is not None and ratio < 0.5 else "ok",
            })
            evidence.append({"kind": "PR", "number": n, "title": title, "url": url, "when": "open"})

        # People whose code they review more than anyone else does.
        authors = self._q("""
            MATCH (p:Human {login: $l})-[c:COLLAB]->(au:Human)
            MATCH (r:Human)-[c2:COLLAB]->(au)
            WITH au, c, max(c2.score) AS best, sum(c2.score) AS total
            WHERE c.score = best AND total > 0 AND c.n >= 2
            RETURN au.login, c.score / total, c.n ORDER BY c.n DESC LIMIT 8
        """, {"l": login})

        orphaned = [a for a in areas if a["status"] == "orphaned"]
        thin = [a for a in areas if a["status"] == "thin"]
        data = {
            "person": login,
            "summary": {"open_prs_stuck": len(stuck),
                        "stuck_prs_without_a_good_backup": sum(1 for x in stuck if x["backup"] != "ok"),
                        "areas_orphaned": len(orphaned), "areas_thin": len(thin),
                        "authors_losing_their_main_reviewer": len(authors)},
            "areas": areas[:10],
            "stuck_prs": sorted(stuck, key=lambda x: ("ok", "weak", "none").index(x["backup"]), reverse=True)[:10],
            "authors_losing_their_main_reviewer": [{"author": a, "share_of_their_reviews": round(sh, 2),
                                                    "reviews": k} for a, sh, k in authors],
        }
        nodes = [pid(login)] + [aid(a["area"]) for a in areas[:10]]
        links = [[pid(login), aid(a["area"]), "risk"] for a in areas[:10]]
        links += [[pid(a["next_person"]), aid(a["area"]), "path"] for a in areas[:10] if a["next_person"]]
        nodes += [pid(a["next_person"]) for a in areas[:10] if a["next_person"]]
        return {"data": data, "evidence": evidence[:6], "highlight": {"nodes": nodes, "links": links}}

    def fading_paths(self, idle_days: int = 90) -> dict:
        """Knowledge that is fading: areas whose main expert ever has stopped walking them,
        with whoever (if anyone) is picking the work up."""
        now = time.time()
        rows = self._q("""
            MATCH (p:Human)-[w:WORN]->(a:Area) WHERE a.depth >= 1 AND a.depth <= 3 AND a.prs >= 6
            WITH a, p, w ORDER BY w.prs DESC, w.score DESC
            WITH a, collect({login: p.login, prs: w.prs, last: w.last, score: w.score}) AS ppl
            WHERE ppl[0].last < $cutoff
            RETURN a.path, a.prs, a.last, a.wear, ppl[0..6] ORDER BY ppl[0].prs DESC LIMIT 30
        """, {"cutoff": int(now) - idle_days * DAY})
        seen_at = dict(self._q("""
            MATCH (p:Human)-[w:WORN]->(:Area {path: '/'}) WHERE p.login IN $l RETURN p.login, w.last
        """, {"l": list({r[4][0]["login"] for r in rows})}))
        out = []
        for path, prs, last, wear, ppl in rows:
            expert = ppl[0]
            still_here = activity_factor(seen_at.get(expert["login"]), now) >= 0.7
            heirs = [q for q in ppl[1:] if activity_factor(q["last"], now) >= 0.7]
            heir = max(heirs, key=lambda q: q["score"], default=None)
            out.append({"area": path, "prs": prs, "expert": expert["login"],
                        "expert_share_of_all_prs": round(expert["prs"] / prs, 2),
                        "expert_last_here": ago(expert["last"]), "area_last_touched": ago(last),
                        "expert_still_active_elsewhere": still_here,
                        "picked_up_by": heir and heir["login"],
                        "heir_share_of_recent_work": heir and round(heir["score"] / wear, 2) if wear else None,
                        "risk": ("only they know it and they have moved on to other code - ask them now"
                         if not heir and still_here else
                         "the expert is gone and nobody active has taken over" if not heir else
                                "handover in progress" if heir["score"] / (wear or 1) >= 0.3 else "thinly covered")})
        seen = {(o["area"], o["expert"]) for o in out}
        out = [o for o in out if "/" not in o["area"] or (o["area"].rsplit("/", 1)[0], o["expert"]) not in seen]
        # Sibling folders with the same expert and the same story read as one finding.
        groups: dict[tuple, list] = {}
        for o in out:
            key = (o["area"].rsplit("/", 1)[0] if "/" in o["area"] else o["area"], o["expert"], o["risk"])
            groups.setdefault(key, []).append(o)
        merged = []
        for (parent, _, _), rows_ in groups.items():
            if len(rows_) < 2:
                merged += rows_
                continue
            head = dict(max(rows_, key=lambda o: o["prs"]))
            head["subareas"] = sorted(o["area"].rsplit("/", 1)[-1] for o in rows_)
            head["paths"] = [o["area"] for o in rows_]
            head["area"], head["prs"] = parent, sum(o["prs"] for o in rows_)
            merged.append(head)
        out = merged
        out.sort(key=lambda o: (o["picked_up_by"] is not None, o["expert_still_active_elsewhere"],
                                -o["expert_share_of_all_prs"] * o["prs"]))
        out = out[:10]
        evidence = [e for o in out[:3] for e in self._evidence_for(o["expert"], o.get("paths", [o["area"]])[0], 1)]
        links = [[pid(o["expert"]), aid(a), "risk"] for o in out for a in o.get("paths", [o["area"]])]
        links += [[pid(o["picked_up_by"]), aid(a), "path"] for o in out if o["picked_up_by"]
                  for a in o.get("paths", [o["area"]])]
        for o in out:
            o.pop("paths", None)
        return {"data": out, "evidence": evidence,
                "highlight": {"nodes": list(dict.fromkeys([k[1] for k in links] + [k[0] for k in links])),
                              "links": links}}

    def onboard(self, area: str, login: str | None = None) -> dict:
        """A newcomer's way into an area: who to ask, which PRs to read first, which areas come with it,
        and (given their login) the shortest chain of review relationships to the right person."""
        path = self.resolve_area(area)
        if not path:
            return {"data": f"no area matches '{area}'", "evidence": [], "highlight": _empty()}
        now = time.time()
        guides = self._q("""
            MATCH (p:Human)-[w:WORN]->(a:Area {path: $a}) WHERE w.last > $active
            RETURN p.login, w.reviewed, w.authored, w.score / a.wear, w.last
            ORDER BY w.reviewed DESC, w.score DESC LIMIT 3
        """, {"a": path, "active": int(now) - 45 * DAY})
        reading = self._q("""
            MATCH (x:PR)-[:IN_AREA]->(:Area {path: $a}) WHERE x.merged > $since
            OPTIONAL MATCH (r:Human)-[rv:REVIEWED]->(x)
            OPTIONAL MATCH (au:Person)-[:AUTHORED]->(x)
            WITH x, au, count(rv) AS discussion
            RETURN x.number, x.title, x.url, x.merged, au.login, discussion, x.additions + x.deletions AS size
            ORDER BY discussion DESC LIMIT 40
        """, {"a": path, "since": int(now) - 240 * DAY})
        # The best first read is small and well discussed: a 10k-line PR teaches a newcomer nothing.
        reading = sorted(reading, key=lambda r: -r[5] / (1 + (r[6] or 0) / 400))[:5]
        nearby = self._q("""
            MATCH (x:PR)-[:IN_AREA]->(a:Area {path: $a}), (x)-[:IN_AREA]->(b:Area)
            WHERE b.depth = a.depth AND b <> a
            RETURN b.path, count(x) AS c ORDER BY c DESC LIMIT 3
        """, {"a": path})
        data = {
            "area": path,
            "ask": [{"login": g, "reviews_here": rv, "authored_here": au, "share_of_recent_work": round(sh, 2),
                     "last_active": ago(last)} for g, rv, au, sh, last in guides]
                   or "nobody has been active here in 45 days",
            "read_first": [{"number": n, "title": t, "url": u, "merged": ago(m), "by": au, "reviews": d,
                            "lines_changed": sz} for n, t, u, m, au, d, sz in reading],
            "usually_changes_with": [{"area": b, "shared_prs": c} for b, c in nearby],
        }
        highlight = {"nodes": [aid(path)] + [pid(g[0]) for g in guides],
                     "links": [[pid(g[0]), aid(path), "worn"] for g in guides]}
        if login and guides:
            intro = self.intro_path(login.lstrip("@"), guides[0][0])
            data["intro_chain"] = intro["data"]
            highlight["nodes"] += intro["highlight"]["nodes"]
            highlight["links"] += intro["highlight"]["links"]
        evidence = [{"kind": "PR", "number": r["number"], "title": r["title"], "url": r["url"], "when": r["merged"]}
                    for r in data["read_first"]]
        return {"data": data, "evidence": evidence, "highlight": highlight}

    # ---------- routing strategies, compared ----------

    def backtest(self, last: int = 300) -> dict:
        from desire_paths.strategies import backtest
        return backtest(self, last)

    def compare_routing(self) -> dict:
        from desire_paths.strategies import compare_routing
        return compare_routing(self)

    def cypher(self, query: str) -> dict:
        """Read-only Cypher escape hatch."""
        if re.search(r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP|CALL\s+db\.idx\.fulltext\.(create|drop))\b", query, re.I):
            return {"data": "only read queries are allowed", "evidence": [], "highlight": _empty()}
        if not re.search(r"\bLIMIT\b", query, re.I):
            query += " LIMIT 25"
        try:
            res = self.g.ro_query(query)
            self.trace.append({"cypher": query, "params": {}, "rows": len(res.result_set), "ms": None})
        except Exception as e:
            return {"data": f"query error: {e}", "evidence": [], "highlight": _empty()}
        rows = [[_plain(v) for v in row] for row in res.result_set[:25]]
        return {"data": {"columns": [h[1] if isinstance(h, list) else h for h in res.header], "rows": rows},
                "evidence": [], "highlight": _empty()}


def _tidy(query: str) -> str:
    lines = [line.rstrip() for line in query.strip("\n").splitlines()]
    pad = min((len(line) - len(line.lstrip()) for line in lines if line.strip()), default=0)
    return "\n".join(line[pad:] for line in lines)


def _short(params: dict) -> dict:
    out = {}
    for k, v in params.items():
        if isinstance(v, list) and len(v) > 6:
            v = v[:6] + [f"... {len(v) - 6} more"]
        out[k] = v
    return out


def _plain(v):
    if hasattr(v, "properties"):
        return v.properties
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v
