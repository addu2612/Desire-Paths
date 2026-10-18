# Desire Paths

**An agent that knows how a software project really works, decides who should review what, and acts on it.**

In urban planning, a *desire path* is the dirt trail people wear across the grass when the paved path goes the wrong way.
Good planners don't put up fences.
They pave the trail.

Every repository has a paved path: `CODEOWNERS`, the team page, the org chart.
And every repository has desire paths: who actually writes, reviews and fixes each part of the code.
Desire Paths loads both into one FalkorDB graph, shows where they disagree, and gives an agent the graph tools to make routing decisions and take action on them.

Built for **Graph Hacks: Context for AI Agents** (WeMakeDevs x FalkorDB), Track 01: *Agents That Act on Connected Data*.

![Apache Airflow: dashed blue is CODEOWNERS, amber is where people actually walk](docs/airflow-map.png)

## What it found on real repos

On **FalkorDB/FalkorDB** (600 merged PRs, Mar 2025 to Oct 2026):

- Only **14%** of files have an official owner. `CODEOWNERS` covers `build/` and `.github/`; the query engine, runtime and tests have no owner on paper.
- **17 of the 40** most recently updated open PRs are misrouted, 16 of them with nobody asked to review. One has waited 53 days.
- Even with load balancing, 56 of 64 suggested reviews land on two people. The review capacity of the project is two humans, and the graph can show it.
- AI reviewers (CodeRabbit, Copilot and others) wrote about 1,600 reviews, more than all humans combined. They are labelled and kept out of the people analysis.

On **apache/airflow**:

- `CODEOWNERS` is dense (31% of files), yet **112 review requests** in the open queue go to people with no recent work in the code those PRs touch.

## What the agent does

**Decide.**
`route_pr` walks a multi-hop path through the graph for a live PR:

```
(:OpenPR)-[:TOUCHES {weight}]->(:Area)<-[:WORN {score, last}]-(:Person)<-[:REQUESTS]-(other :OpenPR)
```

It picks a primary reviewer by fit (share of recent, decayed work in the touched areas, weighted by how much of the change lands there), discounted by inactivity and by current review load, with a small bonus for having reviewed the author before.
If the primary does most of the work in the main area, the second reviewer is chosen to **spread knowledge** instead of piling on.
Every decision comes with the chain of evidence, the PRs behind it, and who was ruled out and why (inactive, overloaded, author).

**Act at scale.**
`triage` sweeps the open review queue and flags PRs that are unrouted, waiting on an absent reviewer, or assigned to someone with no history in that code.
Suggestions are assigned greedily with simulated load, so the queue spreads across the team.

**Act.**
The agent proposes; a person approves with one click; the server checks GitHub write permission and executes:

- request reviewers on the PR, or
- open a pull request with a `CODEOWNERS` file "paved" from the desire paths (on your fork if you can't push upstream).

Without write access it records a dry run and changes nothing.
Every attempt is stored in the graph as an `(:Action)` node linked to the PR, so the agent knows what it already did.

![A routing decision with its reasoning and an approve button](docs/route-decision.png)

**Explain and answer.**
Ask in plain language: *Who actually knows the execution plan? What breaks if @x goes on vacation? Where does CODEOWNERS disagree with reality?*
The agent picks tools, walks the graph, cites PRs, and the map lights up the exact path it reasoned along.

## The signature detail: the worn-grass map

- **Dashed blue** lines are the blueprint: what `CODEOWNERS` says.
- **Amber** paths are worn by real pull requests and reviews. Thicker means more work; brighter means more recent. Particles walk along paths used in the last 45 days.
- **Ghost owners** (dashed circles) own code on paper and are absent in practice.
- Press **play** to watch 18 months of the repo wear in: paths form, thicken, and fade as people move on. The decay is computed in the browser from the same per-PR events the backend uses.
- **Pave the paths** turns the amber trails into solid blue and drafts the `CODEOWNERS` that would make the blueprint match reality.

## Why a graph

Each question is a traversal, not a lookup:

| Question | Graph work |
|---|---|
| Who should review PR #3120? | PR -> files -> areas -> people -> their open review load |
| Who actually knows `src/execution_plan`? | recency-decayed `WORN` edges vs. `OFFICIAL` edges |
| Who are the hidden bottlenecks? | `algo.betweenness` on the reviewer -> author graph |
| Whose review matters most? | `algo.pageRank` on `COLLAB` |
| Which groups never review each other? | `algo.labelPropagation` communities |
| How do I get an intro to @x? | `algo.SPpaths` over review relationships |
| Who knows "vector index"? | full-text index on PR titles, then traverse to people |

### Graph model

```
Blueprint   (:Team|:Person)-[:OWNS]->(:File)            from CODEOWNERS (last match wins)
            (:Team|:Person)-[:OFFICIAL {coverage}]->(:Area)

Activity    (:Person)-[:AUTHORED|MERGED]->(:PR)
            (:Person)-[:REVIEWED {state, at}]->(:PR)
            (:PR)-[:TOUCHED]->(:File)-[:IN]->(:Area)-[:IN]->(:Area)
            (:PR)-[:CLOSES]->(:Issue)<-[:OPENED]-(:Person)

Derived     (:Person)-[:WORN {score, prs, last}]->(:Area)    recency decay, 120-day half-life
(Cypher)    (:Person)-[:COLLAB {n, score}]->(:Person)       reviewer -> author

Live queue  (:Person)-[:OPENED_PR]->(:OpenPR)-[:TOUCHES {weight}]->(:Area)
            (:OpenPR)-[:REQUESTS]->(:Person|:Team)
            (:Action {kind, status, at})-[:FOR]->(:OpenPR)
```

Humans carry a `:Human` label and bots a `:Bot` label, so graph algorithms can be scoped to people.
Each repository is its own isolated FalkorDB graph.

## MCP

All tools are exposed over MCP, so any agent can use the graph.

- HTTP: `https://<your-deployment>/mcp` (streamable HTTP)
- stdio: `uv run desire-paths-mcp`

Claude Desktop / Cursor config:

```json
{
  "mcpServers": {
    "desire-paths": { "command": "uv", "args": ["--directory", "/path/to/Desire-Paths", "run", "desire-paths-mcp"] }
  }
}
```

Tools: `list_repos`, `who_knows`, `who_knows_topic`, `route_pr`, `triage`, `blueprint_drift`, `bus_factor`, `hidden_bottlenecks`, `person`, `intro_path`, `pave`, `request_reviewers` (plan by default, `confirm=true` to execute).

## Run it locally

Requirements: Docker, [uv](https://docs.astral.sh/uv/), and the GitHub CLI logged in (or a `GITHUB_TOKEN`).

```bash
docker run -d -p 6379:6379 falkordb/falkordb:latest
cp .env.example .env              # add an LLM key (see below)
uv sync
uv run python -m desire_paths.graph   # builds graphs from the cached snapshots in data/
uv run desire-paths                   # http://localhost:8000
```

Load any public repo from the UI ("Walk it"), or from the shell:

```bash
uv run python -m desire_paths.graph owner/name
```

Or run everything in one container (FalkorDB + app):

```bash
docker build -t desire-paths .
docker run -p 8000:8000 --env-file .env desire-paths
```

### LLM

Any OpenAI-compatible endpoint works; the agent uses native tool calls and falls back to JSON tool requests for models that lack them.

| `LLM_PROVIDER` | Endpoint | Default model |
|---|---|---|
| `sarvam` | Sarvam v2 | `glm5.3` |
| `subq` | Subquadratic | `subq-1m-preview` |
| `ollama` | local Ollama | `gemma4:e2b` |
| `custom` | `LLM_BASE_URL` | `LLM_MODEL` |

`auto` (the default) picks the first provider with a key and falls back to local Ollama.

## Project layout

```
src/desire_paths/
  github.py      GitHub GraphQL: merged PRs, reviews, files, issues, open PRs, CODEOWNERS
  graph.py       builds both layers in FalkorDB and derives desire paths with Cypher
  tools.py       graph tools: routing, triage, drift, centrality, communities, paths
  actions.py     permission-checked GitHub writes, recorded as (:Action) nodes
  agent.py       tool-calling agent loop over any OpenAI-compatible model
  mcp_server.py  the same tools over MCP
  server.py      FastAPI: graph view, streaming agent, actions, ingest, /mcp
web/             the worn-grass map (force-graph on canvas) and the agent panel
data/            cached GitHub snapshots so the demo runs offline
```
