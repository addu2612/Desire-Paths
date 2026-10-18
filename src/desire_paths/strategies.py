"""Routing strategies, measured against each other.

backtest         - replay merged PRs: hide who reviewed, ask each strategy, score the guess.
                   Only activity from before each PR was opened is used, so nothing leaks.
compare_routing  - the open queue today: CODEOWNERS vs. best graph fit vs. load-balanced,
                   where the balanced plan is a max-flow problem solved by FalkorDB's algo.maxFlow.
"""

from __future__ import annotations

import math
import time
import uuid
from bisect import bisect_left
from collections import Counter, defaultdict

from desire_paths.graph import HALF_LIFE_DAYS, area_weights
from desire_paths.tools import DAY, GraphTools, activity_factor, ago, aid, pid

TAU = HALF_LIFE_DAYS * DAY / math.log(2)
WARMUP_DAYS = 60   # history a PR needs behind it before it is a fair test
BUSY_WINDOW = 90   # "busiest reviewer" baseline looks back this far
LOAD_WINDOW = 14   # balanced strategy: assignments still on someone's plate


class _Decayed:
    """Recency-decayed sums over a stream of (time, weight) events, answerable at any point in time."""

    def __init__(self, events: list[tuple[int, float]], base: int):
        events.sort()
        self.times = [t for t, _ in events]
        self.cum, acc = [], 0.0
        for t, w in events:
            acc += w * math.exp((t - base) / TAU)
            self.cum.append(acc)
        self.base = base

    def at(self, t0: float) -> tuple[float, int | None]:
        i = bisect_left(self.times, t0)
        if not i:
            return 0.0, None
        return self.cum[i - 1] * math.exp(-(t0 - self.base) / TAU), self.times[i - 1]


def backtest(t: GraphTools, last: int = 300) -> dict:
    prs = {n: {"created": c, "merged": m, "author": au, "url": u, "title": ti}
           for n, c, m, au, u, ti in t._q("""
               MATCH (x:PR) OPTIONAL MATCH (au:Person)-[:AUTHORED]->(x)
               RETURN x.number, x.created, x.merged, au.login, x.url, x.title
           """)}
    acts = t._q("""
        MATCH (p:Human)-[r:AUTHORED|REVIEWED|MERGED]->(x:PR)
        RETURN p.login, x.number, max(CASE type(r) WHEN 'AUTHORED' THEN 1.0 WHEN 'REVIEWED' THEN
               CASE r.state WHEN 'APPROVED' THEN 0.6 WHEN 'CHANGES_REQUESTED' THEN 0.7 ELSE 0.35 END
               ELSE 0.2 END)
    """)
    reviewers = defaultdict(set)
    for n, login in t._q("MATCH (p:Human)-[:REVIEWED]->(x:PR) RETURN DISTINCT x.number, p.login"):
        if login != prs[n]["author"]:
            reviewers[n].add(login)
    files = defaultdict(list)
    for n, path, changed in t._q("MATCH (x:PR)-[t:TOUCHED]->(f:File) RETURN x.number, f.path, "
                                 "coalesce(t.additions, 0) + coalesce(t.deletions, 0)"):
        files[n].append((path, changed))
    owners = defaultdict(Counter)
    for n, owner in t._q("""
        MATCH (x:PR)-[:TOUCHED]->(f:File)<-[:OWNS]-(o:Person) RETURN x.number, o.login
    """):
        owners[n][owner] += 1
    existing = {a for (a,) in t._q("MATCH (a:Area) RETURN a.path")}

    # Per (person, area) and per area: decayed wear, answerable at any moment in the past.
    base = min(p["merged"] for p in prs.values() if p["merged"])
    weights = {n: area_weights(files[n], existing) for n in prs}
    pa, ar = defaultdict(list), defaultdict(list)
    review_times, pair_times = defaultdict(list), defaultdict(list)
    for login, n, w in acts:
        m = prs[n]["merged"]
        if not m:
            continue
        for a in weights[n]:
            pa[(login, a)].append((m, w))
            ar[a].append((m, w))
        if login in reviewers[n]:
            review_times[login].append(m)
            if prs[n]["author"]:
                pair_times[(login, prs[n]["author"])].append(m)
    wear = {k: _Decayed(v, base) for k, v in pa.items()}
    area_wear = {k: _Decayed(v, base) for k, v in ar.items()}
    walkers = defaultdict(set)
    for login, a in pa:
        walkers[a].add(login)
    for d in (review_times, pair_times):
        for v in d.values():
            v.sort()

    def graph_scores(n: int, t0: float) -> dict[str, float]:
        author, fit, seen = prs[n]["author"], defaultdict(float), {}
        for a, tw in weights[n].items():
            total, _ = area_wear[a].at(t0) if a in area_wear else (0, None)
            if total <= 0:
                continue
            for login in walkers[a]:
                if login == author:
                    continue
                s, lst = wear[(login, a)].at(t0)
                if s > 0:
                    fit[login] += tw * s / total
                    seen[login] = max(seen.get(login) or 0, lst or 0)
        out = {}
        for login, f in fit.items():
            familiar = bisect_left(pair_times.get((login, author), []), t0)
            out[login] = f * activity_factor(seen[login], t0) * (1 + 0.06 * min(familiar, 5))
        return out

    start = base + WARMUP_DAYS * DAY
    tests = sorted((n for n, p in prs.items() if reviewers[n] and p["created"] and p["created"] >= start),
                   key=lambda n: prs[n]["created"])[-last:]

    picks = {"codeowners": {}, "busiest": {}, "graph": {}, "balanced": {}}
    assigned: list[tuple[int, str]] = []
    for n in tests:
        t0, author = prs[n]["created"], prs[n]["author"]
        picks["codeowners"][n] = [o for o, _ in owners[n].most_common() if o != author][:2]
        busy = Counter({login: bisect_left(ts, t0) - bisect_left(ts, t0 - BUSY_WINDOW * DAY)
                        for login, ts in review_times.items() if login != author})
        picks["busiest"][n] = [login for login, c in busy.most_common(2) if c > 0]
        scores = graph_scores(n, t0)
        picks["graph"][n] = sorted(scores, key=lambda k: -scores[k])[:2]
        # Same scores, discounted by what this strategy itself has handed out in the last two weeks.
        load = Counter(login for at, login in assigned if t0 - LOAD_WINDOW * DAY <= at < t0)
        balanced = sorted(scores, key=lambda k: -scores[k] / (1 + 0.12 * 2 * load[k]))[:2]
        picks["balanced"][n] = balanced
        assigned += [(t0, login) for login in balanced]

    labels = {"codeowners": "CODEOWNERS", "busiest": "Two busiest reviewers",
              "graph": "Desire paths (graph)", "balanced": "Desire paths + load balancing"}
    rows = []
    for key, by_pr in picks.items():
        counts = Counter(x for v in by_pr.values() for x in v)
        total = sum(counts.values()) or 1
        rows.append({
            "strategy": labels[key], "key": key,
            "hit_at_1": round(sum(bool(v) and v[0] in reviewers[n] for n, v in by_pr.items()) / len(tests), 3),
            "hit_at_2": round(sum(bool(set(v) & reviewers[n]) for n, v in by_pr.items()) / len(tests), 3),
            "made_a_pick": round(sum(bool(v) for v in by_pr.values()) / len(tests), 3),
            "top_two_share_of_picks": round(sum(c for _, c in counts.most_common(2)) / total, 2),
            "people_used": len(counts),
        })
    actual = Counter(x for n in tests for x in reviewers[n])
    examples = [n for n in reversed(tests)
                if set(picks["graph"][n][:1]) & reviewers[n] and not set(picks["codeowners"][n]) & reviewers[n]][:4]
    return {
        "data": {
            "prs_tested": len(tests),
            "from": ago(prs[tests[0]]["created"]) if tests else None,
            "method": "For each merged PR, rebuild every person's desire paths from activity before the PR "
                      "was opened, hide who reviewed it, and check whether a strategy's top picks include "
                      "a person who actually reviewed it.",
            "strategies": rows,
            "actual_reviews": {"top_two_share": round(sum(c for _, c in actual.most_common(2)) /
                                                      (sum(actual.values()) or 1), 2),
                               "people": len(actual)},
            "examples": [{"number": n, "title": prs[n]["title"], "url": prs[n]["url"],
                          "graph_picked": picks["graph"][n], "codeowners_picked": picks["codeowners"][n] or "nobody",
                          "actually_reviewed": sorted(reviewers[n])} for n in examples],
        },
        "evidence": [{"kind": "PR", "number": n, "title": prs[n]["title"], "url": prs[n]["url"], "when": "merged"}
                     for n in examples],
        "highlight": {"nodes": [], "links": []},
    }


def _flow_assign(t: GraphTools, options: dict[int, list[dict]]) -> tuple[dict[int, str], int, float]:
    """One accountable reviewer per PR, with no one carrying more than they must.

    Flow network: source -> PR (cap 1) -> qualified reviewer (cap 1) -> sink (cap K).
    algo.maxFlow gives the most PRs that can be covered; the smallest K that still covers
    them all is the fairest possible plan. It runs on a scratch graph that is dropped afterwards.
    """
    people = sorted({c["login"] for opts in options.values() for c in opts})
    if not people:
        return {}, 0, 0.0
    name = f"flow_{uuid.uuid4().hex[:10]}"
    fg = type(t.g)(t.g.client, name)
    started = time.perf_counter()

    def q(query: str, params: dict | None = None, note: bool = True):
        res = fg.query(query, params or {})
        if note:
            t.trace.append({"cypher": query, "params": {k: (v if not isinstance(v, list) else f"[{len(v)} rows]")
                                                          for k, v in (params or {}).items()},
                            "rows": len(res.result_set), "ms": None})
        return res

    try:
        q("CREATE (:Src), (:Snk)")
        q("UNWIND $p AS l CREATE (:R {login: l})", {"p": people})
        q("UNWIND $n AS n CREATE (:Q {n: n})", {"n": list(options)})
        q("MATCH (s:Src), (x:Q) CREATE (s)-[:F {cap: 1}]->(x)")
        q("UNWIND $e AS e MATCH (x:Q {n: e[0]}), (r:R {login: e[1]}) "
          "CREATE (x)-[:F {cap: 1, n: e[0], login: e[1]}]->(r)",
          {"e": [[n, c["login"]] for n, opts in options.items() for c in opts]})
        q("MATCH (r:R), (k:Snk) CREATE (r)-[:F {cap: $k}]->(k)", {"k": len(options)})
        solve = """
            MATCH (s:Src), (k:Snk)
            CALL algo.maxFlow({sourceNodes: [s], targetNodes: [k], relationshipTypes: ['F'], capacityProperty: 'cap'})
            YIELD maxFlow, edges, edgeFlows
            RETURN maxFlow, edges, edgeFlows
        """
        best = q(solve).result_set[0][0]
        lo, hi = 1, len(options)
        while lo < hi:  # smallest per-person cap that still covers every coverable PR
            mid = (lo + hi) // 2
            q("MATCH (:R)-[e:F]->(:Snk) SET e.cap = $k", {"k": mid}, note=False)
            if q(solve, note=False).result_set[0][0] >= best:
                hi = mid
            else:
                lo = mid + 1
        q("MATCH (:R)-[e:F]->(:Snk) SET e.cap = $k", {"k": lo})
        _, edges, flows = q(solve).result_set[0]
        plan = {e.properties["n"]: e.properties["login"] for e, f in zip(edges, flows, strict=True)
                if f > 0.5 and "login" in e.properties}
    finally:
        try:
            fg.delete()
        except Exception:
            pass
    # Among plans that respect the cap, prefer better fit: move a PR to a stronger reviewer with room.
    load = Counter(plan.values())
    for n, opts in options.items():
        cur = plan.get(n)
        for c in opts:  # opts are best-first
            if c["login"] == cur:
                break
            if load[c["login"]] < lo:
                if cur:
                    load[cur] -= 1
                plan[n] = c["login"]
                load[c["login"]] += 1
                break
    return plan, lo, round((time.perf_counter() - started) * 1000, 1)


def compare_routing(t: GraphTools, limit: int = 40) -> dict:
    now = time.time()
    queue = t._q("""
        MATCH (o:OpenPR) WHERE NOT o.draft AND o.updated > $since
        OPTIONAL MATCH (au:Person)-[:OPENED_PR]->(o)
        RETURN o.number, o.title, o.url, au.login, o.updated AS updated ORDER BY updated DESC LIMIT $k
    """, {"since": int(now) - 60 * DAY, "k": limit})
    info = {n: {"title": ti, "url": u, "author": au} for n, ti, u, au, _ in queue}

    cands, options = {}, {}
    for n in info:
        active = [c for c in t._candidates(n, 12) if activity_factor(c["last"], now) >= 0.7]
        cands[n] = {c["login"]: c for c in active}
        if active:
            top = active[0]["score"]
            options[n] = [c for c in active if c["score"] >= 0.35 * top][:5]

    naive = {}
    for n, owner, label in t._q("""
        UNWIND $ns AS n
        MATCH (o:OpenPR {number: n})-[tc:TOUCHES]->(a:Area)<-[off:OFFICIAL]-(own)
        WITH n, own, sum(tc.weight * off.coverage) AS w ORDER BY w DESC
        WITH n, collect(own)[0] AS own
        RETURN n, coalesce(own.login, own.slug), labels(own)[0]
    """, {"ns": list(info)}):
        naive[n] = owner if label != "Team" else None  # a team request names nobody accountable
    graph = {n: next(iter(c), None) for n, c in cands.items()}
    balanced, cap, flow_ms = _flow_assign(t, options)

    def score(plan: dict) -> dict:
        picked = {n: plan.get(n) for n in info}
        counts = Counter(v for v in picked.values() if v)
        top = counts.most_common(1)
        fits = [cands[n][v]["fit"] / max(c["fit"] for c in cands[n].values())
                for n, v in picked.items() if v and v in cands[n]]
        return {
            "prs_with_an_accountable_reviewer": sum(1 for v in picked.values() if v),
            "reviewer_has_recent_work_there": sum(1 for n, v in picked.items() if v and v in cands[n]),
            "average_fit_vs_best": round(sum(fits) / len(fits), 2) if fits else 0,
            "most_on_one_person": top[0][1] if top else 0,
            "busiest": top[0][0] if top else None,
            "people_used": len(counts),
        }

    rows = [{"strategy": "CODEOWNERS", "key": "codeowners", **score(naive)},
            {"strategy": "Desire paths (graph)", "key": "graph", **score(graph)},
            {"strategy": "Desire paths + max-flow balancing", "key": "balanced", **score(balanced)}]
    loads = Counter(balanced.values())
    plan = [{"number": n, "title": info[n]["title"], "url": info[n]["url"], "codeowners": naive.get(n),
             "graph": graph.get(n), "balanced": balanced.get(n)} for n in info if graph.get(n) or naive.get(n)]
    nodes = [pid(x) for x in loads] + [aid(c["via"][0]["area"]) for n, x in balanced.items()
                                       for c in [cands[n][x]] if x in cands[n]]
    links = [[pid(x), aid(cands[n][x]["via"][0]["area"]), "path"] for n, x in balanced.items() if x in cands[n]]
    return {
        "data": {
            "open_prs": len(info),
            "strategies": rows,
            "max_flow": {"per_person_cap": cap, "prs_covered": len(balanced), "solve_ms": flow_ms,
                         "network": "source -> PR (1) -> qualified reviewer (1) -> sink (cap)",
                         "qualified": "active in the touched areas and at least 35% of the PR's best fit"},
            "balanced_load": dict(loads.most_common()),
            "plan": plan[:20],
        },
        "evidence": [],
        "highlight": {"nodes": list(dict.fromkeys(nodes)), "links": links},
    }
