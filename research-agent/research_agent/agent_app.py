"""Flower AgentApp wrapper for the CohortGuard research pipeline.

Boundary: this AgentApp only reads public research APIs/cache and local hand-
structured criteria files. It has no connector, tool, or code path for clinic
agents or clinical data.
"""
from __future__ import annotations

import copy
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

from research.candidates import build_candidates
from research.http_cache import DEFAULT_CACHE_DIR, CacheMiss, Http
from research.sources import NCT_RE
from research.web import fetch_page_summary

try:  # Flower is provided in the AgentApp runtime; local tests run without it.
    from flwr.agentapp import AgentApp, AgentSession
    from flwr.app import Context
except ModuleNotFoundError:  # pragma: no cover - exercised only outside Flower.
    AgentSession = Any  # type: ignore
    Context = Any  # type: ignore

    class AgentApp:  # minimal decorator shim for local import/tests
        def main(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
            def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
                return fn
            return deco


MODEL = "openai/gpt-5.6-sol"
DEFAULT_HTTP_MODE = "replay"
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AGENT_CACHE_DIR = DEFAULT_CACHE_DIR

# Seeded hallucination for the demo: well-formed but nonexistent. The grounding
# gate must strike it. Enabled with run_config demo_seed_fake=true.
DEMO_FAKE_CANDIDATE = {
    "intervention": "Glucoreversin",
    "mechanism": None,
    "trial": {"nct_id": "NCT99999999", "title": "(model-suggested trial)",
              "phase": "PHASE3", "status": "RECRUITING", "primary_endpoint": None},
    "evidence": [],
    "evidence_level": "phase3",
    "relevance": "Suggested by model; unverified.",
    "score": 99,
    "criteria": [],
}

app = AgentApp()


def _response_text(response: Any) -> str:
    """Extract text from common OpenAI Responses/Flower response shapes."""
    text = getattr(response, "output_text", None)
    if isinstance(text, str):
        return text
    output = getattr(response, "output", None)
    if output is None and isinstance(response, dict):
        text = response.get("output_text")
        if isinstance(text, str):
            return text
        output = response.get("output")
    parts: list[str] = []
    for item in output or []:
        if hasattr(item, "to_dict"):
            item = item.to_dict()
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                parts.append(content["text"])
        if isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "".join(parts)


def make_llm(agent: AgentSession, run_config: dict[str, Any]) -> Callable[[str], str]:
    """Wrap Flower model access as llm(prompt) -> str.

    Flower 1.39 docs show model calls through the OpenAI SDK using the runtime
    FLWR_RUNTIME_BASE_URL/API_KEY. Some templates expose an agent.responses
    helper; use it when present, otherwise use the documented OpenAI client.
    """
    model = str(run_config.get("model", MODEL))

    client: Any = None

    def llm(prompt: str) -> str:
        nonlocal client
        responses = getattr(agent, "responses", None)
        if responses is not None and hasattr(responses, "create"):
            return _response_text(responses.create(model=model, input=prompt))

        if client is None:
            from openai import OpenAI  # imported only inside the AgentApp runtime

            client = OpenAI(
                base_url=os.environ["FLWR_RUNTIME_BASE_URL"],
                api_key=os.environ["FLWR_RUNTIME_API_KEY"],
                max_retries=0,
            )
        return _response_text(client.responses.create(model=model, input=prompt))

    return llm


def make_web_fetcher(agent: AgentSession) -> Callable[[str], tuple[int, str]]:
    """Return an Http fetcher backed by Flower's web_fetch connector.

    Use this only if direct outbound HTTP is blocked by the runtime. The exact
    connector output is runtime-provided, so this accepts several plain JSON
    shapes containing status/body/text/content.
    """
    def fetch(url: str) -> tuple[int, str]:
        tool_call = {
            "type": "function_call",
            "name": "web_fetch",
            "call_id": "cohortguard_web_fetch",
            "arguments": json.dumps({"url": url}),
        }
        out = agent.connectors.call(tool_call)
        if hasattr(out, "to_dict"):
            out = out.to_dict()
        if isinstance(out, dict) and isinstance(out.get("output"), str):
            try:
                out = json.loads(out["output"])
            except json.JSONDecodeError:
                return 200, out["output"]
        if isinstance(out, dict):
            status = int(out.get("status") or out.get("status_code") or 200)
            body = out.get("body", out.get("text", out.get("content", "")))
            return status, body if isinstance(body, str) else json.dumps(body)
        return 200, str(out)

    return fetch


@lru_cache(maxsize=None)
def _read_cached_criteria(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def load_cached_criteria(nct_id: str, cache_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    if not isinstance(nct_id, str) or not NCT_RE.match(nct_id):
        raise ValueError("nct_id must match NCT followed by 8 digits")
    path = Path(cache_dir or DEFAULT_AGENT_CACHE_DIR) / f"criteria_{nct_id}.json"
    return copy.deepcopy(_read_cached_criteria(path))


# Options a request may set in its "options" field. In Flower Chat there is no per-run
# config, so the coordinator (or a person testing in chat) passes switches this way.
REQUEST_OPTIONS = {"http_mode", "demo_seed_fake", "with_mechanism", "use_web_fetch", "model"}


def _truthy(v: Any) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() in {"1", "true", "yes"})


ERROR_DETAILS = {
    "not_in_cache": "The requested replay data is unavailable.",
    "unknown_trial": "Structured criteria are unavailable for that trial.",
    "bad_request": "The request is invalid.",
    "internal_error": "The request could not be completed.",
}


def _error(code: str) -> dict[str, str]:
    return {"type": "error", "error": code, "detail": ERROR_DETAILS[code]}


def safe_handle_request(request: Any, **kw: Any) -> dict[str, Any]:
    """Return stable JSON errors without exposing exception text."""
    try:
        if not isinstance(request, dict):
            raise ValueError("the prompt must be a JSON object")
        options = request.get("options") or {}
        if not isinstance(options, dict):
            raise ValueError("options must be a JSON object")
        unknown = set(options) - REQUEST_OPTIONS
        if unknown:
            raise ValueError(f"unknown options: {sorted(unknown)}")
        kw["run_config"] = {**(kw.get("run_config") or {}), **options}
        return handle_request(request, **kw)
    except CacheMiss:
        return _error("not_in_cache")
    except FileNotFoundError:
        return _error("unknown_trial")
    except (ValueError, json.JSONDecodeError, TypeError, KeyError, IndexError):
        return _error("bad_request")
    except Exception:
        return _error("internal_error")


def handle_request(
    request: dict[str, Any],
    run_config: Optional[dict[str, Any]] = None,
    agent: Optional[AgentSession] = None,
    llm: Optional[Callable[[str], str]] = None,
) -> dict[str, Any]:
    """Pure/testable request handler for the AgentApp main function."""
    run_config = run_config or {}
    req_type = request.get("type")
    cache_dir = run_config.get("cache_dir") or DEFAULT_AGENT_CACHE_DIR

    if req_type == "trial_criteria":
        nct_id = request.get("nct_id")
        if not isinstance(nct_id, str):
            raise ValueError("trial_criteria requires string nct_id")
        data = load_cached_criteria(nct_id, cache_dir)
        return {"type": "trial_criteria", "trial": data.get("trial"), "criteria": data.get("criteria", [])}

    if req_type == "pipeline":
        condition = request.get("condition")
        outcome_keywords = request.get("outcome_keywords")
        if not isinstance(condition, str) or not isinstance(outcome_keywords, list) or not all(isinstance(x, str) for x in outcome_keywords):
            raise ValueError("pipeline requires condition str and outcome_keywords [str]")
        condition = " ".join(condition.lower().split())
        mode = str(run_config.get("http_mode", run_config.get("http.mode", DEFAULT_HTTP_MODE)))
        use_web_fetch = bool(run_config.get("use_web_fetch", False))
        fetcher = make_web_fetcher(agent) if use_web_fetch and agent is not None else None
        http = Http(mode=mode, cache_dir=cache_dir, fetcher=fetcher)
        log: list[dict[str, Any]] = []
        model_llm = llm if llm is not None else (make_llm(agent, run_config) if agent is not None else None)
        extra = [json.loads(json.dumps(DEMO_FAKE_CANDIDATE))] if _truthy(run_config.get("demo_seed_fake")) else []
        if not _truthy(run_config.get("with_mechanism", True)):
            model_llm = None
        candidates = build_candidates(http, condition, outcome_keywords, llm=model_llm, log=log,
                                      extra_candidates=extra)
        blocked = [{"nct_id": e["nct_id"], "intervention": e["intervention"], "reasons": e["reasons"]}
                   for e in log if e.get("event") == "grounding_check" and not e.get("passed")]
        return {"type": "pipeline", "candidates": candidates, "blocked": blocked, "grounding_log": log}

    if req_type == "web_page":
        mode = str(run_config.get("http_mode", run_config.get("http.mode", DEFAULT_HTTP_MODE)))
        use_web_fetch = _truthy(run_config.get("use_web_fetch"))
        fetcher = make_web_fetcher(agent) if use_web_fetch and agent is not None else None
        http = Http(mode=mode, cache_dir=cache_dir, fetcher=fetcher)
        model_llm = llm if llm is not None else (make_llm(agent, run_config) if agent is not None else None)
        return fetch_page_summary(http, request.get("url"), llm=model_llm)

    raise ValueError("request type must be 'pipeline', 'trial_criteria' or 'web_page'")


@app.main()
def main(agent: AgentSession, context: Context) -> None:
    """Run the research pipeline from context.run_config['agent.input']."""
    run_config = dict(getattr(context, "run_config", {}) or {})
    raw = run_config.get("agent.input")
    if raw in (None, "", "{}"):
        raw = getattr(agent, "prompt", "{}")
    try:
        request = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        request = None
    result = safe_handle_request(request, run_config=run_config, agent=agent)
    result_text = json.dumps(result, indent=2)
    events = getattr(agent, "events", None)
    if events is not None and hasattr(events, "emit"):
        events.emit({"type": "response.output_text.delta", "delta": result_text})
        events.emit({"type": "response.completed"})
    print(result_text)
