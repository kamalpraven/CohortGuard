"""Site feasibility: a gated count of potentially eligible patients.

This is the function each clinic node calls (M1 wires it into the Grid).

    feasibility_count("B", "NCT07060456", criteria, patients, gate)
    -> {"nct_id": "NCT07060456", "clinic": "B", "eligible_n": 84,
        "unchecked_criteria": [...], "status": "ok", ...}

A patient counts if they meet every checkable inclusion criterion and no
checkable exclusion criterion. Criteria with checkable=false are skipped and
listed as unchecked. The raw count goes through the privacy gate: suppressed
below the minimum cell size, Laplace noise added, charged to the clinic's budget.
"""

from __future__ import annotations

from .criteria import is_potentially_eligible, unchecked_criteria
from .privacy import PrivacyGate
from .templates import BadRequest, validate_criteria


def feasibility_count(
    clinic: str,
    nct_id: str | None,
    criteria: list[dict],
    patients: list[dict],
    gate: PrivacyGate,
) -> dict:
    validate_criteria(criteria)
    raw = sum(is_potentially_eligible(criteria, p) for p in patients)
    rel = gate.release({"eligible_n": raw}, gate_on=raw)
    resp = {"nct_id": nct_id, "clinic": clinic, "unchecked_criteria": unchecked_criteria(criteria)}
    if rel["suppressed"]:
        return {**resp, "eligible_n": None, "status": "suppressed", "reason": rel["reason"]}
    return {**resp, "eligible_n": rel["counts"]["eligible_n"], "status": "ok",
            "noise_scale": rel["noise_scale"], "budget_remaining": rel["budget_remaining"]}


__all__ = ["feasibility_count", "BadRequest"]
