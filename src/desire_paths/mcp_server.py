"""Desire Paths as an MCP server, so any agent (Claude, Cursor, ...) can use the graph tools.

  stdio:  uv run desire-paths-mcp
  HTTP:   mounted by the web server at /mcp
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from desire_paths import actions as A  # noqa: E402
from desire_paths import graph as G  # noqa: E402
from desire_paths.tools import GraphTools  # noqa: E402

mcp = MCPServer(
    name="desire-paths",
    title="Desire Paths",
    instructions=(
        "Graph tools over a GitHub repo loaded into FalkorDB. Two layers: the blueprint (CODEOWNERS) and the "
        "desire paths (who actually authored and reviewed recent work). Every tool takes `repo` as owner/name; "
        "call list_repos first. Results include `evidence` (PR links) to cite. Write actions default to a plan; "
        "only pass confirm=true after the user approves."
    ),
)
_db = None
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


def _tools(repo: str) -> GraphTools:
    global _db
    _db = _db or G.connect()
    name = G.graph_name(repo)
    if name not in _db.list_graphs():
        raise ValueError(f"{repo} is not loaded; call list_repos")
    return GraphTools(_db.select_graph(name), repo)


def _out(result: dict) -> dict:
    return {"data": result["data"], "evidence": result.get("evidence", [])}


@mcp.tool(annotations=READ)
def list_repos() -> list[str]:
    """Repositories loaded into the graph."""
    global _db
    _db = _db or G.connect()
    return [g.replace("__", "/", 1) for g in _db.list_graphs() if "__" in g]


@mcp.tool(annotations=READ)
def who_knows(repo: str, area: str) -> dict:
    """Official CODEOWNERS vs. the people who actually worked on a directory recently, with evidence PRs."""
    return _out(_tools(repo).who_knows(area))


@mcp.tool(annotations=READ)
def who_knows_topic(repo: str, topic: str) -> dict:
    """Full-text search PR titles for a concept, then follow the graph to the people behind them."""
    return _out(_tools(repo).who_knows_topic(topic))


@mcp.tool(annotations=READ)
def route_pr(repo: str, number: int) -> dict:
    """Decide who should review an open PR (PR -> touched areas -> people who work there -> review load).
    Returns primary and second reviewer with reasons, who was ruled out, and a proposed action."""
    return _out(_tools(repo).route_pr(number))


@mcp.tool(annotations=READ)
def triage(repo: str) -> dict:
    """Sweep the open review queue for PRs with no reviewer, absent or wrong reviewers, or long waits."""
    return _out(_tools(repo).triage())


@mcp.tool(annotations=READ)
def blueprint_drift(repo: str) -> dict:
    """Where CODEOWNERS disagrees with reality: busy areas with no owner, and owners who are absent."""
    return _out(_tools(repo).blueprint_drift())


@mcp.tool(annotations=READ)
def bus_factor(repo: str) -> dict:
    """Areas where one person does most of the recent work."""
    return _out(_tools(repo).bus_factor())


@mcp.tool(annotations=READ)
def hidden_bottlenecks(repo: str) -> dict:
    """Betweenness centrality on the review graph: people who connect everyone else."""
    return _out(_tools(repo).hidden_bottlenecks())


@mcp.tool(annotations=READ)
def person(repo: str, login: str) -> dict:
    """What a person actually works on, who they review with, and what they officially own."""
    return _out(_tools(repo).person(login))


@mcp.tool(annotations=READ)
def intro_path(repo: str, from_login: str, to_login: str) -> dict:
    """Shortest chain of review relationships between two people."""
    return _out(_tools(repo).intro_path(from_login, to_login))


@mcp.tool(annotations=READ)
def pave(repo: str) -> dict:
    """Draft a CODEOWNERS file from the desire paths, with the changes versus today."""
    return _out(_tools(repo).pave())


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True, openWorldHint=True))
def request_reviewers(repo: str, number: int, reviewers: list[str] | None = None, confirm: bool = False) -> dict:
    """Request reviewers on GitHub for an open PR. Without confirm=true this only returns the plan.
    Defaults to the reviewers route_pr picks. Needs write access to the repo."""
    tools = _tools(repo)
    decision = tools.route_pr(number, live=False)["data"]
    if isinstance(decision, str):
        return {"status": "failed", "error": decision}
    picks = reviewers or decision["proposed_action"]["reviewers"]
    return A.request_reviewers(tools.g, repo, number, picks, decision, confirm)


def main():
    mcp.run("stdio")


if __name__ == "__main__":
    main()
