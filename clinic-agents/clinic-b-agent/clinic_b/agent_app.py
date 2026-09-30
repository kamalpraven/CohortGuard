"""Clinic B AgentApp: the outside clinic.

Clinic B's patient JSON stays inside this app. The agent reads it, computes the
requested template, and releases only privacy-gated aggregates (min cell size,
Laplace noise, persistent budget ledger) for the coordinator. Only the fixed
response schema leaves; no record, identifier or free text is relayed.
"""

from __future__ import annotations

import os
import random
from pathlib import Path

from flwr.agentapp import AgentApp, AgentSession
from flwr.app import Context

from clinic_core.agent_common import egress, emit_result, request_from
from .clinic import handle_clinic_b
from clinic_core.privacy import BudgetLedger, PrivacyGate
from clinic_core.store import load_patients

DATA_DIR = Path(__file__).parent / "data"
LEDGER = Path(os.environ.get("CLINIC_B_STATE", Path.home() / ".cohortguard" / "clinic_b_budget.json"))

app = AgentApp()


@app.main()
def main(agent: AgentSession, context: Context) -> None:
    """Aggregate Clinic B's data for one request and send back the gated result."""
    req = request_from(agent)
    if req is None:
        emit_result(agent, {"clinic": "B", "status": "rejected", "reason": "no_valid_request"})
        return
    gate = PrivacyGate(BudgetLedger(LEDGER), random.SystemRandom())
    resp = handle_clinic_b(req, load_patients("B", DATA_DIR), gate)
    emit_result(agent, egress(resp))
