"""Clinic A deployment AgentApp: release only privacy-gated aggregates.

Exact patient-level work is available only through the explicit local CLI and
never through Flower events or SuperLink run logs.
"""

from __future__ import annotations

import os
import random
from pathlib import Path

from flwr.agentapp import AgentApp, AgentSession
from flwr.app import Context

from clinic_core.agent_common import egress, emit_result, request_from
from clinic_core.aggregate import handle_gated_aggregate
from clinic_core.privacy import BudgetLedger, PrivacyGate
from clinic_core.store import load_patients

DATA_DIR = Path(__file__).parent / "data"
LEDGER = Path(os.environ.get("CLINIC_A_STATE", Path.home() / ".cohortguard" / "clinic_a_budget.json"))

app = AgentApp()


@app.main()
def main(agent: AgentSession, context: Context) -> None:
    """Answer one structured request with a privacy-gated aggregate."""
    req = request_from(agent)
    if req is None:
        emit_result(agent, {"clinic": "A", "status": "rejected", "reason": "no_valid_request"})
        return
    try:
        gate = PrivacyGate(BudgetLedger(LEDGER), random.SystemRandom())
        resp = handle_gated_aggregate(req, load_patients("A", DATA_DIR), gate, clinic="A")
    except Exception:
        resp = {"clinic": "A", "status": "error", "reason": "internal_error"}
    emit_result(agent, egress(resp))
