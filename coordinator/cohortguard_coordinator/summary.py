"""Final-summary model call for the SuperLink coordinator."""

from __future__ import annotations

import json
import os
import re
from typing import Any


def _response_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if isinstance(text, str):
        return text
    if isinstance(response, dict) and isinstance(response.get("output_text"), str):
        return response["output_text"]
    raise RuntimeError("model_returned_no_text")


_OUTSIDE_FACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bmechanism(?:s)?\b"),
    re.compile(r"(?i)\b(?:cardiovascular|renal|kidney|heart failure)\b"),
    re.compile(r"(?i)\b(?:lowers?|reduces?|improves?)\s+(?:glucose|blood sugar|hba1c|weight)\b"),
    re.compile(r"(?i)\b(?:protective|preferred|recommended|standard of care)\b"),
)


def assert_no_obvious_outside_facts(text: str, allowed_source: str = "") -> None:
    """Fail closed on common clinical/background claims absent from computed results."""
    allowed = allowed_source.casefold()
    for pattern in _OUTSIDE_FACT_PATTERNS:
        for match in pattern.finditer(text):
            if match.group(0).casefold() not in allowed:
                raise ValueError("outside_fact_detected")


def summary_instructions(workflow: str) -> str:
    """Return workflow-specific instructions for the final-summary model."""
    base = (
        "Write a concise clinical-research summary using only facts present in the computed result. "
        "Do not add drug mechanisms, clinical background, guideline context, safety claims, or outside knowledge. "
        "If context would help but is not in the computed result, say that context is not included in the result. "
        "Do not reconstruct identifiers, invent counts, infer unstated budget details, "
        "or make operational/privacy-process claims unless they are explicitly in the computed result. "
    )
    if workflow == "cohort_question":
        return base + (
            "For cohort comparisons, describe n values as noised cohort sizes, not eligibility counts. "
            "Frame comparisons as observational associations in synthetic aggregate data, not causal effects. "
            "If a rate_difference object is present, include its 95% confidence interval and method exactly from the computed result. "
            "Report privacy budget only if explicitly present in the computed result."
        )
    if workflow == "site_feasibility":
        return base + (
            "For site feasibility, describe released counts as noised estimates of potentially eligible upper-bound screening counts, "
            "not exact patient counts. Report unchecked criteria and privacy budget only exactly as the clinics stated them."
        )
    if workflow == "trial_pipeline":
        return base + (
            "For trial pipeline results, summarize the returned public trial candidates, evidence notes, and blocked items. "
            "Do not add differential-privacy or patient-count caveats unless they appear in the computed result."
        )
    return base


def write_final_summary(
    request: dict[str, Any], result: dict[str, Any], model: str, reasoning_effort: str = "low"
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
        reasoning={"effort": reasoning_effort or "low"},
        instructions=summary_instructions(str(result.get("workflow", request.get("workflow", "")))),
        input=prompt,
    )
    text = _response_text(response).strip()
    assert_no_obvious_outside_facts(text, prompt)
    return text
