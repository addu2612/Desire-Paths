"""Fetch merged PRs, reviews, touched files, linked issues and CODEOWNERS from GitHub.

Raw results are cached as JSON under data/ so the graph can be rebuilt offline.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
API = "https://api.github.com"

PR_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: MERGED, first: 40, after: $cursor,
                 orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url createdAt mergedAt additions deletions
        author { login }
        mergedBy { login }
        files(first: 60) { nodes { path additions deletions } }
        reviews(first: 30) { nodes { author { login } state submittedAt } }
        closingIssuesReferences(first: 5) {
          nodes { number title url createdAt author { login } }
        }
      }
    }
  }
}
"""


OPEN_PR_FIELDS = """
  number title url createdAt updatedAt isDraft additions deletions
  author { login }
  files(first: 80) { nodes { path additions deletions } }
  reviewRequests(first: 10) {
    nodes { requestedReviewer { ... on User { login } ... on Team { combinedSlug } } }
  }
  reviews(first: 30) { nodes { author { login } state submittedAt } }
"""

OPEN_PRS_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 40, after: $cursor, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { %s }
    }
  }
}
""" % OPEN_PR_FIELDS

ONE_PR_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) { state %s } }
}
""" % OPEN_PR_FIELDS


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    return subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, check=True
    ).stdout.strip()


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"Authorization": f"Bearer {_token()}", "Accept": "application/vnd.github+json"},
        timeout=60,
    )


def fetch_codeowners(client: httpx.Client, owner: str, name: str) -> str:
    for path in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"):
        r = client.get(f"{API}/repos/{owner}/{name}/contents/{path}")
        if r.status_code == 200:
            return base64.b64decode(r.json()["content"]).decode()
    return ""


def fetch_prs(client: httpx.Client, owner: str, name: str, limit: int) -> list[dict]:
    prs: list[dict] = []
    cursor = None
    while len(prs) < limit:
        r = client.post(
            f"{API}/graphql",
            json={"query": PR_QUERY, "variables": {"owner": owner, "name": name, "cursor": cursor}},
        )
        r.raise_for_status()
        body = r.json()
        if "errors" in body:
            raise RuntimeError(body["errors"])
        page = body["data"]["repository"]["pullRequests"]
        prs.extend(page["nodes"])
        print(f"  fetched {len(prs)} PRs", file=sys.stderr)
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return prs[:limit]


def _graphql(client: httpx.Client, query: str, variables: dict) -> dict:
    r = client.post(f"{API}/graphql", json={"query": query, "variables": variables})
    r.raise_for_status()
    body = r.json()
    if "errors" in body:
        raise RuntimeError(body["errors"])
    return body["data"]


def fetch_open_prs(client: httpx.Client, owner: str, name: str, limit: int) -> list[dict]:
    prs: list[dict] = []
    cursor = None
    while len(prs) < limit:
        page = _graphql(client, OPEN_PRS_QUERY, {"owner": owner, "name": name, "cursor": cursor})
        page = page["repository"]["pullRequests"]
        prs.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return prs[:limit]


def fetch_pr(repo: str, number: int) -> dict | None:
    """One PR, live from GitHub (used to route PRs that aren't in the snapshot)."""
    owner, name = repo.split("/")
    with _client() as client:
        return _graphql(client, ONE_PR_QUERY, {"owner": owner, "name": name, "number": number})[
            "repository"]["pullRequest"]


def cache_path(repo: str) -> Path:
    return DATA_DIR / f"{repo.replace('/', '__')}.json"


def fetch_repo(repo: str, limit: int = 600) -> dict:
    owner, name = repo.split("/")
    with _client() as client:
        snapshot = {
            "repo": repo,
            "codeowners": fetch_codeowners(client, owner, name),
            "prs": fetch_prs(client, owner, name, limit),
            "open_prs": fetch_open_prs(client, owner, name, 150),
            "fetched_at": int(time.time()),
        }
    DATA_DIR.mkdir(exist_ok=True)
    cache_path(repo).write_text(json.dumps(snapshot))
    return snapshot


def load_snapshot(repo: str, refresh: bool = False, limit: int = 600) -> dict:
    path = cache_path(repo)
    if path.exists() and not refresh:
        snap = json.loads(path.read_text())
        if "open_prs" not in snap:  # older cache: add open PRs without refetching history
            owner, name = repo.split("/")
            with _client() as client:
                snap["open_prs"] = fetch_open_prs(client, owner, name, 150)
            path.write_text(json.dumps(snap))
        return snap
    return fetch_repo(repo, limit)
