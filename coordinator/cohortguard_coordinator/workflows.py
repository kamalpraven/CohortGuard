"""Deterministic coordinator workflows and aggregate pooling."""

from __future__ import annotations

import re
import math
from typing import Any

from shared.allowlist import DIAGNOSIS_CODES, MEDICATION_CLASSES, SEX_VALUES

from .grid import CoordinatorGrid
from .identifiers import assert_deidentified, safe_json

_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_WORKFLOWS = {"cohort_question", "trial_pipeline", "site_feasibility"}


def validate_coordinator_request(value: Any) -> dict[str, Any]:
    """Validate the de-identified Doctor Agent request before Grid/model access."""
    if not isinstance(value, dict) or value.get("sanitized") is not True:
        raise ValueError("unsanitized_request")
    assert_deidentified(value)
    request_id = value.get("request_id")
    workflow = value.get("workflow")
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise ValueError("invalid_request")
    if workflow not in _WORKFLOWS:
        raise ValueError("invalid_request")
    if not isinstance(value.get("question"), str) or not value["question"].strip():
        raise ValueError("invalid_request")
    return value


def _single_reply(replies: dict[str, dict[str, Any]], node_id: str) -> dict[str, Any]:
    if set(replies) != {node_id}:
        raise RuntimeError("invalid_reply_source")
    return replies[node_id]


def _request_one(
    grid: CoordinatorGrid, node_id: str, request: dict[str, Any]
) -> dict[str, Any]:
    return _single_reply(grid.request_many({node_id: safe_json(request)}), node_id)


def _clinic_request_many(
    grid: CoordinatorGrid,
    roles: dict[str, str],
    request: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    node_ids = {roles["clinic_a"], roles["clinic_b"]}
    # Structured trial criteria can contain public proper names in source text.
    replies = grid.request_many(
        {node_id: safe_json(request, include_names=False) for node_id in node_ids}
    )
    if set(replies) != node_ids:
        raise RuntimeError("invalid_reply_source")
    return replies


def _validate_cohort_params(request: dict[str, Any]) -> dict[str, Any]:
    cohorts = request.get("cohorts")
    filters = request.get("filters") or {}
    if (
        request.get("cohort_field") != "medication"
        or not isinstance(cohorts, list)
        or not cohorts
        or not set(cohorts) <= MEDICATION_CLASSES
        or request.get("outcome") != "readmit_30d"
        or not isinstance(filters, dict)
    ):
        raise ValueError("invalid_request")
    if filters.get("diagnosis") not in DIAGNOSIS_CODES | {None}:
        raise ValueError("invalid_request")
    if filters.get("sex") not in SEX_VALUES | {None}:
        raise ValueError("invalid_request")
    return {
        "template": "outcome_rate_by_cohort",
        "cohort_field": "medication",
        "cohorts": cohorts,
        "outcome": "readmit_30d",
        "filters": filters,
    }


def _pool_cohorts(replies: dict[str, dict[str, Any]]) -> dict[str, Any]:
    statuses = [reply.get("status") for reply in replies.values()]
    if any(status != "ok" for status in statuses):
        return {"status": "partial", "sites": list(replies.values())}
    pooled: dict[str, dict[str, int]] = {}
    for reply in replies.values():
        for row in reply.get("results", []):
            cohort = row["cohort"]
            item = pooled.setdefault(cohort, {"n": 0, "events": 0})
            item["n"] += int(row["n"])
            item["events"] += int(row["events"])
    results = [
        {
            "cohort": cohort,
            "n": values["n"],
            "events": values["events"],
            "rate_pct": round(100 * values["events"] / values["n"], 1)
            if values["n"]
            else None,
        }
        for cohort, values in pooled.items()
    ]
    output: dict[str, Any] = {"status": "ok", "results": results, "site_count": len(replies)}
    if len(results) == 2 and all(row["n"] for row in results):
        first, second = results
        p1 = first["events"] / first["n"]
        p2 = second["events"] / second["n"]
        diff = p1 - p2
        se = math.sqrt((p1 * (1 - p1) / first["n"]) + (p2 * (1 - p2) / second["n"]))
        output["rate_difference"] = {
            "cohort_a": first["cohort"],
            "cohort_b": second["cohort"],
            "difference_pct_points": round(diff * 100, 1),
            "ci_95_pct_points": [round((diff - 1.96 * se) * 100, 1), round((diff + 1.96 * se) * 100, 1)],
            "method": "wald_difference_in_proportions_on_released_counts",
        }
    return output


def run_workflow(
    grid: CoordinatorGrid,
    roles: dict[str, str],
    request: dict[str, Any],
) -> dict[str, Any]:
    """Execute one workflow with all routing and pooling controlled by code."""
    workflow = request["workflow"]
    request_id = request["request_id"]
    if workflow == "cohort_question":
        clinic_request = {"request_id": request_id, **_validate_cohort_params(request)}
        replies = _clinic_request_many(grid, roles, clinic_request)
        return {"workflow": workflow, **_pool_cohorts(replies)}

    if workflow == "trial_pipeline":
        condition = request.get("condition")
        keywords = request.get("outcome_keywords")
        if not isinstance(condition, str) or not isinstance(keywords, list) or not all(
            isinstance(item, str) for item in keywords
        ):
            raise ValueError("invalid_request")
        research_request = {
            "type": "pipeline",
            "condition": condition,
            "outcome_keywords": keywords,
            "options": {"with_mechanism": False},
        }
        result = _request_one(grid, roles["research"], research_request)
        return {"workflow": workflow, "research": result}

    nct_id = request.get("nct_id")
    if not isinstance(nct_id, str) or not re.fullmatch(r"NCT\d{8}", nct_id):
        raise ValueError("invalid_request")
    criteria_result = _request_one(
        grid, roles["research"], {"type": "trial_criteria", "nct_id": nct_id}
    )
    if criteria_result.get("type") != "trial_criteria":
        raise RuntimeError("criteria_unavailable")
    clinic_request = {
        "request_id": request_id,
        "template": "feasibility_count",
        "nct_id": nct_id,
        "criteria": criteria_result.get("criteria", []),
    }
    replies = _clinic_request_many(grid, roles, clinic_request)
    ok = [reply for reply in replies.values() if reply.get("status") == "ok"]
    pooled_count = sum(int(reply["eligible_n"]) for reply in ok)
    return {
        "workflow": workflow,
        "nct_id": nct_id,
        "status": "ok" if len(ok) == len(replies) else "partial",
        "eligible_n": pooled_count if ok else None,
        "sites": list(replies.values()),
    }
