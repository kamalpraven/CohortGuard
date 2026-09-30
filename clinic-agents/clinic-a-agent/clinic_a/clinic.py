"""Clinic A request handler: the in-house clinic.

Exact answers, no gate, nothing is aggregated or sent out.
"""

from __future__ import annotations

from clinic_core.criteria import checklist, is_potentially_eligible, unchecked_criteria
from clinic_core.feasibility import feasibility_count
from clinic_core.privacy import PrivacyGate
from clinic_core.templates import (
    BadRequest,
    cohort_counts,
    cohort_filters,
    criteria_from,
    request_id_from,
)



def _find_patient(req: dict, patients: list[dict]) -> dict:
    matches = [p for p in patients
               if (req.get("mrn") and p["mrn"] == req["mrn"])
               or (req.get("name") and p["name"].lower() == req["name"].lower())]
    if len(matches) != 1:
        raise BadRequest(f"expected exactly one patient, found {len(matches)}")
    return matches[0]


def _next_step(summary: dict) -> str:
    if summary["not_met"]:
        return (f"Likely ineligible; {summary['unknown']} criteria unknown. "
                "Discuss with trial coordinator.")
    if summary["unknown"]:
        return (f"No checkable criterion failed; {summary['unknown']} unknown. "
                "Confirm with trial coordinator.")
    return "All criteria met. Discuss with trial coordinator."


def handle_clinic_a(req: dict, patients: list[dict], gate: PrivacyGate | None = None) -> dict:
    base = {"request_id": None, "clinic": "A", "scope": "in_house"}
    try:
        base["request_id"] = request_id_from(req)
        template = req.get("template")
        if template == "patient_checklist":
            criteria = criteria_from(req)
            p = _find_patient(req, patients)
            result = checklist(criteria, p)
            return {**base, "status": "ok", "template": template, "mrn": p["mrn"],
                    "nct_id": req.get("nct_id"), **result,
                    "next_step": _next_step(result["summary"])}
        if template == "feasibility_local":
            criteria = criteria_from(req)
            eligible = [p["mrn"] for p in patients if is_potentially_eligible(criteria, p)]
            return {**base, "status": "ok", "template": template,
                    "nct_id": req.get("nct_id"), "eligible_n": len(eligible),
                    "eligible_mrns": eligible,
                    "unchecked_criteria": unchecked_criteria(criteria)}
        if template == "feasibility_count":
            # Gated count for the coordinator (site feasibility). Same gate as Clinic B.
            if gate is None:
                raise BadRequest("feasibility_count needs the privacy gate")
            return {**base, "scope": "gated", **feasibility_count(
                "A", req.get("nct_id"), criteria_from(req), patients, gate)}
        if template == "outcome_rate_by_cohort":
            filters, _ = cohort_filters(req)
            return {**base, "status": "ok", "template": template,
                    "results": cohort_counts(req, patients, filters)}
        raise BadRequest("unknown template")
    except BadRequest:
        return {**base, "status": "rejected", "reason": "invalid_request"}


