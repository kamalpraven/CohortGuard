"""Demo question sets and expected outcomes shared by the local and SuperGrid demo scripts.

Each demo set runs against its own ledger session (``start_*.sh --session NAME``):

- ``main``: 2.0 + 2.0 + 0.5 = 4.5 of 5.0 epsilon per clinic. The Maria case runs second
  so it shows both redaction and a released result before the budget is spent.
- ``sglt2_suppression``: a fresh session, so the SGLT2 comparison is suppressed because
  its counts are too small, not because the budget is empty.
"""

from __future__ import annotations

from typing import Any

DEMO_SETS = ("main", "sglt2_suppression")
BUDGET_TOTAL = 5.0


def demo_questions(maria: dict[str, Any], demo: str) -> dict[str, str]:
    """Return ordered ``label -> raw doctor question`` for one demo set."""
    if demo == "main":
        return {
            "cohort_question": "Compare readmission cohorts for metformin and basal insulin in type 2 diabetes across Clinic A and Clinic B.",
            "maria_scrubbed_cohort": (
                f"For {maria['name']} also written Delgado, Maria, MRN {maria['mrn']}, "
                f"DOB {maria['dob']}, compare readmission cohorts for metformin and basal insulin in type 2 diabetes."
            ),
            "trial_pipeline": "Find trials for type 2 diabetes with readmission or hospitalization outcomes.",
            "site_feasibility": "Estimate site feasibility for NCT07060456 in type 2 diabetes across Clinic A and Clinic B.",
        }
    if demo == "sglt2_suppression":
        return {
            "sglt2_expected_suppressed": "Compare readmission cohorts for SGLT2 and sulfonylurea in type 2 diabetes age 50-59 across Clinic A and Clinic B.",
        }
    raise ValueError(f"unknown demo set: {demo}")


def check_outcome(label: str, request: dict[str, Any], result: dict[str, Any]) -> None:
    """Fail loudly when a demo step does not show what it is meant to show."""
    if result.get("workflow") != request["workflow"]:
        raise RuntimeError(f"{label} routed to {result.get('workflow')}, expected {request['workflow']}")
    if label in {"cohort_question", "maria_scrubbed_cohort"}:
        if result.get("status") != "ok" or result.get("site_count") != 2:
            raise RuntimeError(f"{label} expected a released two-site result, got status {result.get('status')}")
    if label == "maria_scrubbed_cohort" and "[REDACTED_IDENTIFIER]" not in request["question"]:
        raise RuntimeError("maria_scrubbed_cohort request was not redacted")
    if label == "sglt2_expected_suppressed":
        reasons = {site.get("reason") for site in result.get("suppressed_sites", [])}
        if result.get("results") or reasons != {"below_disclosure_threshold"}:
            raise RuntimeError(f"{label} expected small-count suppression at both sites, got {sorted(map(str, reasons))}")
    if label == "site_feasibility":
        remaining = {site.get("budget_remaining") for site in result.get("sites", [])}
        # budget_remaining is the fraction of the 5.0 total: 0.5 left after 4.5 spent.
        if remaining != {round(0.5 / BUDGET_TOTAL, 3)}:
            raise RuntimeError(f"{label} expected 4.5 of 5.0 spent at both clinics, got remaining {remaining}")
