"""Final-summary model call for the SuperLink coordinator."""

from __future__ import annotations

import json
import os
from typing import Any


def _response_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if isinstance(text, str):
        return text
    if isinstance(response, dict) and isinstance(response.get("output_text"), str):
        return response["output_text"]
    raise RuntimeError("model_returned_no_text")


def write_final_summary(
    request: dict[str, Any], result: dict[str, Any], model: str
) -> str:
    """Ask the configured model to summarize an already-computed result."""
    if not model:
        return json.dumps(result, separators=(",", ":"), ensure_ascii=False)
    from openai import OpenAI

    client = OpenAI(
        base_url=os.environ["FLWR_RUNTIME_BASE_URL"],
        api_key=os.environ["FLWR_RUNTIME_API_KEY"],
        max_retries=0,
    )
    prompt = json.dumps(
        {"deidentified_question": request["question"], "computed_result": result},
        ensure_ascii=False,
    )
    response = client.responses.create(
        model=model,
        instructions=(
            "Write a concise clinical-research summary using only the computed result. "
            "Describe released clinic counts as noised estimates of potentially eligible upper-bound screening counts, "
            "not exact patient counts. Report privacy budget only exactly as the clinics stated it in the computed result. "
            "Do not infer patient-level facts, reconstruct identifiers, invent counts, or infer unstated budget details."
        ),
        input=prompt,
    )
    return _response_text(response).strip()
