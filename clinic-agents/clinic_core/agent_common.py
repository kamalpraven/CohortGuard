"""Glue shared by the Clinic A and Clinic B AgentApps.

The model's only job is turning a natural-language prompt into a template
request. Handlers validate that request; nothing the model writes reaches data.
"""

from __future__ import annotations

import json
import os
from typing import Any

# MiniMax-M3 on Nebius Token Factory (the proposal's clinic-agent model). Override
# per run with `flwr run -c model=...` (see [tool.flwr.app.config]). The starter's
# model was "openai/gpt-5.6-sol".
DEFAULT_MODEL = "MiniMaxAI/MiniMax-M3"


def extract_request(prompt: str) -> dict | None:
    """Pull a JSON object out of the prompt (structured coordinator message)."""
    dec = json.JSONDecoder()
    for i, ch in enumerate(prompt):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(prompt[i:])
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


def translate_with_model(prompt: str, guide: str, model: str = DEFAULT_MODEL) -> dict | None:
    """Ask the runtime model for a template request as JSON; None if it fails."""
    from openai import OpenAI

    client = OpenAI(
        base_url=os.environ["FLWR_RUNTIME_BASE_URL"],
        api_key=os.environ["FLWR_RUNTIME_API_KEY"],
        max_retries=0,
    )
    resp = client.responses.create(
        model=model,
        instructions=guide + "\nReply with ONE JSON object and nothing else.",
        input=prompt,
    )
    return extract_request(resp.output_text)


def emit_result(agent: Any, payload: dict) -> None:
    """Show the result in the frontend and print it as the run's final text."""
    text = json.dumps(payload)
    agent.events.emit({"type": "response.output_text.delta", "delta": text})
    agent.events.emit({"type": "response.completed"})
    print(text)


def request_from(agent: Any, guide: str, context: Any = None) -> dict | None:
    """Structured JSON in the prompt wins; otherwise ask the model (fail closed)."""
    found = extract_request(agent.prompt)
    if found is not None:
        return found
    run_config = getattr(context, "run_config", None) or {}
    model = str(run_config.get("model", DEFAULT_MODEL))
    try:
        return translate_with_model(agent.prompt, guide, model)
    except Exception:   # no model configured / provider error: fail closed
        return None


# Fixed output schema for anything that leaves a clinic: other keys are dropped.
EGRESS_FIELDS = {
    "request_id", "clinic", "status", "nct_id", "eligible_n", "unchecked_criteria",
    "results", "counter_offer", "reason", "noise_scale", "budget_remaining", "definitions",
}


def egress(resp: dict) -> dict:
    return {k: v for k, v in resp.items() if k in EGRESS_FIELDS}
