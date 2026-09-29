"""Deterministic evaluation of M3 structured trial criteria against patients.

Criterion format and semantics follow HANDOFF_M2_data_privacy.md. Everything
here is plain code: no model decides whether a patient matches.
"""

from __future__ import annotations

from typing import Any

from shared.allowlist import ALLOWLIST

MET, NOT_MET, UNKNOWN = "met", "not_met", "unknown"

# Allowlist field -> key in the patient record (scalar fields and list fields).
_PATIENT_KEY = {
    "age": "age",
    "sex": "sex",
    "hba1c": "hba1c",
    "egfr": "egfr",
    "bmi": "bmi",
    "diagnosis": "diagnoses",
    "current_medication": "current_medications",
    "prior_medication": "prior_medications",
}


def validate_criterion(c: dict[str, Any]) -> None:
    """Fail closed if a checkable criterion is outside the allowlist."""
    if not c.get("checkable"):
        return
    field, op = c.get("field"), c.get("op")
    if field not in ALLOWLIST:
        raise ValueError(f"field not allowlisted: {field!r}")
    if op not in ALLOWLIST[field][1]:
        raise ValueError(f"op {op!r} not allowed for field {field!r}")


def _satisfies(c: dict[str, Any], patient: dict[str, Any]) -> bool | None:
    """True/False if the criterion's condition holds, None if data missing."""
    value = patient.get(_PATIENT_KEY[c["field"]])
    if value is None:
        return None
    op, target = c["op"], c["value"]
    if op == "between":
        return target[0] <= value <= target[1]
    if op == "gte":
        return value >= target
    if op == "lte":
        return value <= target
    if op == "eq":
        return target in value if isinstance(value, list) else value == target
    if op == "in":
        held = value if isinstance(value, list) else [value]
        return any(v in target for v in held)
    if op == "has":
        return target in value
    if op == "not_has":
        return target not in value
    raise ValueError(f"unknown op: {op!r}")


def check_criterion(c: dict[str, Any], patient: dict[str, Any]) -> str:
    """met / not_met / unknown for one criterion (exclusions are inverted)."""
    if not c.get("checkable"):
        return UNKNOWN
    validate_criterion(c)
    ok = _satisfies(c, patient)
    if ok is None:
        return UNKNOWN
    if c.get("exclusion"):
        ok = not ok
    return MET if ok else NOT_MET


def checklist(criteria: list[dict[str, Any]], patient: dict[str, Any]) -> dict[str, Any]:
    """Per-criterion met / not_met / unknown checklist plus summary counts."""
    rows = [
        {"source_text": c["source_text"], "status": check_criterion(c, patient)}
        for c in criteria
    ]
    summary = {s: sum(r["status"] == s for r in rows) for s in (MET, NOT_MET, UNKNOWN)}
    return {"criteria": rows, "summary": summary}


def is_potentially_eligible(criteria: list[dict[str, Any]], patient: dict[str, Any]) -> bool:
    """Every checkable inclusion met and no checkable exclusion hit.

    A checkable inclusion with missing data does not count as met. A checkable
    exclusion with missing data does not count as hit.
    """
    for c in criteria:
        if not c.get("checkable"):
            continue
        status = check_criterion(c, patient)
        if status == NOT_MET:
            return False
        if status == UNKNOWN and not c.get("exclusion"):
            return False
    return True


def unchecked_criteria(criteria: list[dict[str, Any]]) -> list[str]:
    """source_text of every criterion that cannot be checked from our data."""
    seen: list[str] = []
    for c in criteria:
        if not c.get("checkable") and c["source_text"] not in seen:
            seen.append(c["source_text"])
    return seen
