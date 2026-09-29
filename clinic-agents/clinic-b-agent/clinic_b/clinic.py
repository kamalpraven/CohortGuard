"""Clinic B request handler: the outside clinic.

Everything leaving goes through the privacy gate.
"""

from __future__ import annotations

from clinic_core.feasibility import feasibility_count
from clinic_core.privacy import PrivacyGate
from clinic_core.templates import BadRequest, cohort_counts, cohort_filters, criteria_from, rate_pct



def _released_row(cohort: str, c: dict) -> dict:
    """One noised cohort row; the rate is derived from the noised counts (no extra budget)."""
    n = c[f"{cohort}.n"]
    events = min(c[f"{cohort}.events"], n)
    return {"cohort": cohort, "n": n, "events": events, "rate_pct": rate_pct(events, n)}


def handle_clinic_b(req: dict, patients: list[dict], gate: PrivacyGate) -> dict:
    base = {"request_id": req.get("request_id"), "clinic": "B"}
    try:
        template = req.get("template")
        if template == "feasibility_count":
            return {**base, **feasibility_count("B", req.get("nct_id"), criteria_from(req),
                                                patients, gate)}
        if template == "outcome_rate_by_cohort":
            filters, counter = cohort_filters(req)
            if counter:
                return {**base, "status": "counter_offer", "results": [],
                        "counter_offer": counter, "reason": "age_band_too_fine"}
            rows = cohort_counts(req, patients, filters)
            flat = {f"{r['cohort']}.{k}": r[k] for r in rows for k in ("n", "events")}
            rel = gate.release(flat, gate_on=min(r["n"] for r in rows))
            if rel["suppressed"]:
                return {**base, "status": "suppressed", "results": [],
                        "reason": rel["reason"]}
            c = rel["counts"]
            return {**base, "status": "ok", "counter_offer": None, "reason": None,
                    "results": [_released_row(r["cohort"], c) for r in rows],
                    "noise_scale": rel["noise_scale"],
                    "budget_remaining": rel["budget_remaining"],
                    "definitions": {"readmit_30d": "unplanned return <= 30 days"}}
        raise BadRequest(f"unknown template {template!r}")
    except BadRequest as e:
        return {**base, "status": "rejected", "reason": str(e)}
