"""Clinic A AgentApp: the in-house clinic.

Clinic A's records never leave the clinic. In-house answers are exact and
unaggregated; the one exception is `feasibility_count`, which goes through the
same privacy gate as Clinic B (noise, suppression below 10, budget). The agent
also answers the clinic's own doctor with exact, patient-level results (e.g. the
single-patient checklist). It has no Grid tools and no web access; it only reads
its local JSON file.
"""

from __future__ import annotations

import os
import random
from pathlib import Path

from flwr.agentapp import AgentApp, AgentSession
from flwr.app import Context

from clinic_core.agent_common import egress, emit_result, request_from
from clinic_core.privacy import BudgetLedger, PrivacyGate
from clinic_core.store import load_patients

from .clinic import handle_clinic_a

DATA_DIR = Path(__file__).parent / "data"
LEDGER = Path(os.environ.get("CLINIC_A_STATE", Path.home() / ".cohortguard" / "clinic_a_budget.json"))

app = AgentApp()


@app.main()
def main(agent: AgentSession, context: Context) -> None:
    """Answer one in-house request from local data."""
    req = request_from(agent)
    if req is None:
        emit_result(agent, {"clinic": "A", "status": "rejected", "reason": "no_valid_request"})
        return
    gate = PrivacyGate(BudgetLedger(LEDGER), random.SystemRandom())
    resp = handle_clinic_a(req, load_patients("A", DATA_DIR), gate)
    # Only the gated feasibility count may leave; everything else stays in-house.
    emit_result(agent, egress(resp) if resp.get("scope") == "gated" else resp)
