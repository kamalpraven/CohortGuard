"""Structured request parsing and fixed-schema output for clinic AgentApps."""

from __future__ import annotations

import json
from typing import Any


def extract_request(prompt: str) -> dict | None:
    """Accept only a complete JSON object; free text fails closed."""
    if not isinstance(prompt, str):
        return None
    try:
        request = json.loads(prompt)
    except (json.JSONDecodeError, TypeError):
        return None
    return request if isinstance(request, dict) else None


def emit_result(agent: Any, payload: dict) -> None:
    """Show the result in the frontend and print it as the run's final text."""
    text = json.dumps(payload)
    agent.events.emit({"type": "response.output_text.delta", "delta": text})
    agent.events.emit({"type": "response.completed"})
    print(text)


def request_from(agent: Any) -> dict | None:
    """Read a structured deployment request without invoking a model."""
    return extract_request(getattr(agent, "prompt", ""))


# Fixed output schema for anything that leaves a clinic: other keys are dropped.
EGRESS_FIELDS = {
    "request_id", "clinic", "status", "nct_id", "eligible_n", "unchecked_criteria",
    "results", "counter_offer", "reason", "noise_scale", "budget_remaining", "definitions",
}


def egress(resp: dict) -> dict:
    return {k: v for k, v in resp.items() if k in EGRESS_FIELDS}
