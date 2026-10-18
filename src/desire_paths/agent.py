"""The Desire Paths agent: an LLM that answers by walking the graph with tools.

Works with any OpenAI-compatible endpoint (Subquadratic, Sarvam, Ollama...).
If a model can't do native tool calls, it falls back to JSON tool requests in plain text.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator

from openai import OpenAI

from desire_paths.tools import GraphTools

MAX_STEPS = 6

PROVIDERS = {
    "subq": ("https://api.subq.ai/v1", "SUBQ_API_KEY", "SUBQ_MODEL", "subq-1m-preview"),
    "sarvam": ("https://api.sarvam.ai/v2", "SARVAM_API_KEY", "SARVAM_MODEL", "glm5.3"),
    "ollama": ("http://localhost:11434/v1", None, "OLLAMA_MODEL", "gemma4:e2b"),
}

SYSTEM = """You are Desire Paths, an agent that knows how a software project REALLY works.

The repository {repo} is loaded into a FalkorDB graph with two layers:
- Blueprint: what CODEOWNERS officially says (Team/Person)-[:OFFICIAL]->(Area).
- Desire paths: what actually happened. (Person)-[:AUTHORED|REVIEWED]->(PR)-[:IN_AREA]->(Area),
  rolled up into (Person)-[:WORN {{score, prs, last}}]->(Area) with recency decay, and
  (reviewer:Person)-[:COLLAB {{n, last}}]->(author:Person).
Areas are directories (Area.path like "src/execution_plan"). Bots are excluded from people analysis.

Rules:
- Always use tools. Never guess names, numbers, or ownership.
- Use who_knows for a directory, who_knows_topic for a concept that is not a directory.
- When official and actual differ, say so plainly: "Officially: ... In practice: ...".
- Cite evidence as markdown links, e.g. [#812](url). Only cite URLs that tools returned.
- Be brief: a direct answer first, then at most 4 short bullets. No preamble. Use plain hyphens, never em dashes.
- Actions: you DECIDE, the user APPROVES. route_pr and pave produce a proposed action that the user
  can approve with a button under your answer. Never claim you requested reviewers or opened a PR.
- For route_pr, explain the decision as a chain: PR -> area -> person, with why, and who was ruled out.
"""

TOOL_SPECS = [
    ("who_knows", "Official CODEOWNERS vs. who actually worked on a directory recently, with evidence PRs.",
     {"area": "directory path or name, e.g. 'src/execution_plan' or 'serializers'"}),
    ("who_knows_topic", "Full-text search PR titles for a concept and find the people behind them.",
     {"topic": "a concept, e.g. 'vector index' or 'memory leak'"}),
    ("find_areas", "List directories whose path contains a string.", {"query": "substring"}),
    ("bus_factor", "Areas where one person does most of the recent work (single points of failure).", {}),
    ("hidden_bottlenecks", "Betweenness centrality on the review graph: people who connect everyone else.", {}),
    ("influence", "PageRank on reviewer->author edges: whose reviews matter most.", {}),
    ("silos", "Community detection on the review graph and how much the groups review each other.", {}),
    ("intro_path", "Shortest chain of review relationships between two people.",
     {"from_login": "GitHub login", "to_login": "GitHub login"}),
    ("person", "What a person actually works on, who they review with, what they officially own.",
     {"login": "GitHub login"}),
    ("blueprint_drift", "Where CODEOWNERS disagrees with reality: unowned busy areas, absent owners.", {}),
    ("pave", "Draft a CODEOWNERS file from the desire paths (pave where people walk). "
             "The UI offers the user a button to open it as a GitHub PR.", {}),
    ("route_pr", "Decide who should review an open pull request: walks PR -> touched areas -> people who "
                 "actually work there -> their current review load, and explains picks and rejections. "
                 "The UI offers the user a button to request those reviewers.",
     {"number": "PR number"}),
    ("triage", "Sweep the open review queue: PRs with nobody asked to review, absent or wrong reviewers, "
               "or long waits, each with suggested reviewers.", {}),
    ("cypher", "Run a read-only Cypher query on the graph for anything the other tools miss.",
     {"query": "a read-only Cypher query"}),
]


TOOL_NAMES = {name for name, _, _ in TOOL_SPECS}


def openai_tools() -> list[dict]:
    return [{
        "type": "function",
        "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": {k: {"type": "string", "description": v}
                                                              for k, v in params.items()},
                           "required": list(params)},
        },
    } for name, desc, params in TOOL_SPECS]


def make_client() -> tuple[OpenAI, str, str]:
    provider = os.environ.get("LLM_PROVIDER", "auto").strip().lower()
    if provider == "auto":  # first provider that has a key, else local Ollama
        provider = next((p for p in ("subq", "sarvam") if os.environ.get(PROVIDERS[p][1])), "ollama")
    if provider == "custom":
        return (OpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ.get("LLM_API_KEY") or "none"),
                os.environ["LLM_MODEL"], provider)
    base, key_env, model_env, default_model = PROVIDERS[provider]
    key = os.environ.get(key_env, "") if key_env else "ollama"
    if not key:
        raise RuntimeError(f"{key_env} is not set in .env")
    return OpenAI(base_url=base, api_key=key, timeout=120), os.environ.get(model_env) or default_model, provider


def _summarise(name: str, args: dict) -> str:
    arg = ", ".join(str(v) for v in args.values())
    return f"{name}({arg})"


def _clean(text: str) -> str:
    # Some reasoning models leak <think> blocks into content.
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


JSON_FALLBACK = """
You cannot call functions natively. To use a tool, reply with ONLY a JSON object on one line:
{{"tool": "<name>", "args": {{...}}}}
When you have enough information, reply with the final answer as normal text (no JSON).
Tools:
{tools}
"""


class Agent:
    def __init__(self, graph, repo: str):
        self.tools = GraphTools(graph, repo)
        self.repo = repo
        self.client, self.model, self.provider = make_client()
        self.native_tools = True

    def _call_tool(self, name: str, args: dict) -> dict:
        empty = {"nodes": [], "links": []}
        if name not in TOOL_NAMES:
            return {"data": f"unknown tool {name}", "evidence": [], "highlight": empty}
        try:
            return getattr(self.tools, name)(**args)
        except Exception as e:  # report tool failures to the model instead of crashing the turn
            return {"data": f"tool error: {e}", "evidence": [], "highlight": empty}

    def _complete(self, messages: list[dict]):
        kwargs = {"model": self.model, "messages": messages, "temperature": 0.2}
        if self.native_tools:
            try:
                return self.client.chat.completions.create(tools=openai_tools(), **kwargs)
            except Exception as e:
                if "tool" not in str(e).lower():
                    raise
                self.native_tools = False  # endpoint rejects tools: switch to JSON mode
                messages[0]["content"] += JSON_FALLBACK.format(
                    tools="\n".join(f"- {n}({', '.join(p)}): {d}" for n, d, p in TOOL_SPECS))
        return self.client.chat.completions.create(**kwargs)

    def run(self, question: str, history: list[dict] | None = None) -> Iterator[dict]:
        messages = [{"role": "system", "content": SYSTEM.format(repo=self.repo)}]
        messages += (history or [])[-6:]
        messages.append({"role": "user", "content": question})
        evidence: dict[str, dict] = {}
        highlight = {"nodes": [], "links": []}

        for _ in range(MAX_STEPS):
            msg = self._complete(messages).choices[0].message
            calls = []
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    calls.append((tc.id, tc.function.name, args))
                messages.append({"role": "assistant", "content": msg.content or "",
                                 "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
            else:
                text = _clean(msg.content)
                m = re.search(r"\{.*\"tool\"\s*:.*\}", text, re.S)
                if m:
                    try:
                        req = json.loads(m.group(0))
                        calls.append((None, req["tool"], req.get("args") or {}))
                        messages.append({"role": "assistant", "content": text})
                    except (json.JSONDecodeError, KeyError):
                        pass
                if not calls:
                    yield {"type": "answer", "text": text, "evidence": list(evidence.values()),
                           "highlight": highlight}
                    return

            for call_id, name, args in calls:
                result = self._call_tool(name, args)
                for e in result.get("evidence", []):
                    evidence[e["url"]] = e
                highlight["nodes"] += result["highlight"]["nodes"]
                highlight["links"] += result["highlight"]["links"]
                step = {"type": "step", "tool": name, "args": args, "label": _summarise(name, args),
                        "highlight": result["highlight"], "evidence": result.get("evidence", [])}
                if name in ("route_pr", "triage", "pave"):
                    step["data"] = result["data"]  # the UI renders decision cards for these
                yield step
                payload = json.dumps({"data": result["data"], "evidence": result.get("evidence", [])},
                                     default=str)[:6000]
                if call_id:
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": payload})
                else:
                    messages.append({"role": "user", "content": f"Tool {name} returned: {payload}"})

        messages.append({"role": "user", "content": "Answer now with what you have."})
        self.native_tools = False
        text = _clean(self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=0.2).choices[0].message.content)
        yield {"type": "answer", "text": text, "evidence": list(evidence.values()), "highlight": highlight}
