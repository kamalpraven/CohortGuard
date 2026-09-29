"""Clinic A AgentApp: the in-house clinic.

Clinic A's records never leave the clinic, so nothing is aggregated, noised or
budgeted here. The agent answers the clinic's own doctor with exact,
patient-level results (e.g. the single-patient eligibility checklist). It has no
Grid tools and no web access; it only reads its local JSON file.
"""

from __future__ import annotations

from pathlib import Path

from flwr.agentapp import AgentApp, AgentSession
from flwr.app import Context

from clinic_core.agent_common import emit_result, request_from
from .clinic import handle_clinic_a
from clinic_core.store import load_patients

DATA_DIR = Path(__file__).parent / "data"

GUIDE = """You route requests for Clinic A's in-house agent. Output a request:
{"template": "patient_checklist", "mrn": "<MRN>" or "name": "<full name>", "nct_id": "NCT########"}
{"template": "feasibility_local", "nct_id": "NCT########"}
{"template": "outcome_rate_by_cohort", "cohort_field": "medication", "cohorts": [...],
 "outcome": "readmit_30d", "filters": {"age_band": "50-59", "diagnosis": "T2D", "sex": "FEMALE"}}
Never invent identifiers. If the user asks for anything else, output {"template": "none"}."""

app = AgentApp()


@app.main()
def main(agent: AgentSession, context: Context) -> None:
    """Answer one in-house request from local data."""
    req = request_from(agent, GUIDE, context)
    if req is None:
        emit_result(agent, {"clinic": "A", "status": "rejected", "reason": "no_valid_request"})
        return
    emit_result(agent, handle_clinic_a(req, load_patients("A", DATA_DIR)))
