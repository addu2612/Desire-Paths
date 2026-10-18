"""Actions the agent can take on GitHub.

Safety model: the LLM can only *propose* an action. A person approves it in the UI, then this
module checks write permission and executes. Without write access it falls back to the user's
fork (for CODEOWNERS PRs) or returns a dry-run plan. Every attempt is recorded in the graph as an
(:Action) node, so the agent can see what it already did.
"""

from __future__ import annotations

import base64
import os
import time

import httpx

from desire_paths.github import API, _client

BRANCH_PREFIX = "desire-paths"


def writes_enabled() -> bool:
    """GitHub writes need an explicit opt-in, so a public deployment can never act with the owner's token."""
    return os.environ.get("ALLOW_WRITE_ACTIONS", "").strip().lower() in ("1", "true", "yes")


def _me(client: httpx.Client) -> str:
    return client.get(f"{API}/user").raise_for_status().json()["login"]


def can_push(client: httpx.Client, repo: str) -> bool:
    r = client.get(f"{API}/repos/{repo}")
    return r.status_code == 200 and bool(r.json().get("permissions", {}).get("push"))


def own_fork(client: httpx.Client, repo: str) -> str | None:
    """The signed-in user's fork of `repo`, if they have one."""
    name = repo.split("/")[1]
    r = client.get(f"{API}/repos/{_me(client)}/{name}")
    if r.status_code == 200 and r.json().get("fork") and r.json().get("parent", {}).get("full_name") == repo:
        return r.json()["full_name"]
    return None


def record(g, kind: str, status: str, target: str, url: str | None, number: int | None, detail: str) -> None:
    g.query("""
        CREATE (x:Action {kind: $k, status: $s, target: $t, url: $u, at: $at, detail: $d})
        WITH x OPTIONAL MATCH (o:OpenPR {number: $n})
        FOREACH (_ IN CASE WHEN o IS NULL THEN [] ELSE [1] END | CREATE (x)-[:FOR]->(o))
    """, {"k": kind, "s": status, "t": target, "u": url, "at": int(time.time()), "d": detail, "n": number})


def routing_comment(decision: dict) -> str:
    d = decision["decision"]
    lines = ["**Suggested reviewers** (from who actually works on the touched code):", ""]
    for key in ("primary", "second"):
        c = d.get(key)
        if c:
            lines.append(f"- @{c['login']}: {round(c['share_of_recent_work_there'] * 100)}% of recent work in "
                         f"`{c['main_area']}` ({c['prs_there']} PRs, last active {c['last_active']})")
    if d.get("why_second"):
        lines.append(f"\n_Second reviewer: {d['why_second']}._")
    lines.append("\n<sub>Drafted by Desire Paths from the repo's FalkorDB activity graph.</sub>")
    return "\n".join(lines)


def request_reviewers(g, repo: str, number: int, reviewers: list[str], decision: dict | None = None,
                      confirm: bool = False) -> dict:
    plan = {
        "kind": "request_reviewers", "repo": repo, "number": number, "reviewers": reviewers,
        "api": f"POST /repos/{repo}/pulls/{number}/requested_reviewers",
        "comment": routing_comment(decision) if decision else None,
    }
    with _client() as client:
        writable = writes_enabled() and can_push(client, repo)
        plan["can_write"] = writable
        plan["writes_enabled"] = writes_enabled()
        if not confirm:
            return {"status": "planned", **plan}
        if not writable:
            record(g, "request_reviewers", "dry-run", repo, None, number, ",".join(reviewers))
            why = "write actions are disabled on this server" if not writes_enabled() else f"no write access to {repo}"
            return {"status": "dry-run", "reason": f"{why}; nothing was changed", **plan}
        r = client.post(f"{API}/repos/{repo}/pulls/{number}/requested_reviewers", json={"reviewers": reviewers})
        if r.status_code >= 300:
            record(g, "request_reviewers", "failed", repo, None, number, r.text[:200])
            return {"status": "failed", "error": r.json().get("message", r.text), **plan}
        url = r.json().get("html_url")
        record(g, "request_reviewers", "done", repo, url, number, ",".join(reviewers))
        return {"status": "done", "url": url, **plan}


def open_codeowners_pr(g, repo: str, codeowners: str, changes: list[dict], confirm: bool = False) -> dict:
    with _client() as client:
        target = (repo if can_push(client, repo) else own_fork(client, repo)) if writes_enabled() else None
        plan = {"kind": "open_codeowners_pr", "repo": repo, "target": target, "writes_enabled": writes_enabled(),
                "files": [".github/CODEOWNERS"], "changes": len(changes)}
        if not confirm:
            return {"status": "planned", **plan}
        if not target:
            record(g, "open_codeowners_pr", "dry-run", repo, None, None, "no write access and no fork")
            why = ("write actions are disabled on this server" if not writes_enabled()
                   else f"no write access to {repo} and no fork in your account")
            return {"status": "dry-run", "reason": why, **plan}

        info = client.get(f"{API}/repos/{target}").raise_for_status().json()
        base = info["default_branch"]
        sha = client.get(f"{API}/repos/{target}/git/ref/heads/{base}").raise_for_status().json()["object"]["sha"]
        branch = f"{BRANCH_PREFIX}/codeowners-{int(time.time())}"
        client.post(f"{API}/repos/{target}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha}).raise_for_status()
        existing = client.get(f"{API}/repos/{target}/contents/.github/CODEOWNERS", params={"ref": branch})
        body = {"message": "Pave the desire paths: CODEOWNERS from actual activity", "branch": branch,
                "content": base64.b64encode(codeowners.encode()).decode()}
        if existing.status_code == 200:
            body["sha"] = existing.json()["sha"]
        client.put(f"{API}/repos/{target}/contents/.github/CODEOWNERS", json=body).raise_for_status()
        table = "\n".join(f"| `{c['area']}/` | {', '.join('@' + x for x in c['today']) if isinstance(c['today'], list) else c['today']} "
                          f"| {', '.join('@' + x for x in c['suggested'])} |" for c in changes[:25])
        pr_body = ("Drafted by **Desire Paths** from who actually authored and reviewed recent work "
                   f"in `{repo}`, using a FalkorDB graph of PRs, reviews and files.\n\n"
                   "| Area | Owner today | Suggested |\n|---|---|---|\n" + table)
        pr = client.post(f"{API}/repos/{target}/pulls", json={
            "title": "Pave the desire paths: CODEOWNERS from actual activity",
            "head": branch, "base": base, "body": pr_body,
        })
        if pr.status_code >= 300:
            record(g, "open_codeowners_pr", "failed", target, None, None, pr.text[:200])
            return {"status": "failed", "error": pr.json().get("message", pr.text), **plan}
        url = pr.json()["html_url"]
        record(g, "open_codeowners_pr", "done", target, url, None, f"{len(changes)} changes")
        return {"status": "done", "url": url, **plan}
