"""Run from repo root after scripts/sync_clinics.sh:  pytest tests"""

import importlib
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Repo root first so tests use the source clinic_core, not the vendored copies.
sys.path[:0] = [str(ROOT), str(ROOT / "clinic_a_agent"), str(ROOT / "clinic_b_agent")]

from clinic_a.clinic import handle_clinic_a  # noqa: E402
from clinic_b.clinic import handle_clinic_b  # noqa: E402
from clinic_b.privacy import MIN_CELL, BudgetLedger, PrivacyGate  # noqa: E402
from clinic_core.store import load_patients  # noqa: E402

DATA = {c: ROOT / f"clinic_{c.lower()}_agent" / f"clinic_{c.lower()}" / "data" for c in "AB"}
A = load_patients("A", DATA["A"])
B = load_patients("B", DATA["B"])
CANARIES = json.loads((ROOT / "data" / "canaries.json").read_text())
FEAS = {"template": "feasibility_count", "nct_id": "NCT07060456"}
COHORT = {"template": "outcome_rate_by_cohort", "cohort_field": "medication",
          "cohorts": ["sglt2_inhibitor", "sulfonylurea"], "outcome": "readmit_30d",
          "filters": {"diagnosis": "T2D", "age_band": "50-59"}}


def gate(tmp_path, seed=1):
    return PrivacyGate(BudgetLedger(tmp_path / "ledger.json"), random.Random(seed))


def test_maria_checklist_matches_m3_expectation():
    r = handle_clinic_a({"template": "patient_checklist", "name": "Maria Delgado",
                         "nct_id": "NCT07060456"}, A)
    assert r["summary"] == {"met": 5, "not_met": 1, "unknown": 8}
    not_met = [c for c in r["criteria"] if c["status"] == "not_met"]
    assert "basal insulin" in not_met[0]["source_text"]


def test_maria_only_in_clinic_a():
    assert any(p["name"] == "Maria Delgado" for p in A)
    assert not any(p["name"] == "Maria Delgado" for p in B)


def test_both_clinics_have_20_plus_eligible():
    a = handle_clinic_a({"template": "feasibility_local", "nct_id": "NCT07060456"}, A)
    assert a["eligible_n"] >= 20 and len(a["eligible_mrns"]) == a["eligible_n"]
    assert "Maria" not in json.dumps(a)
    assert len(a["unchecked_criteria"]) == 8


def test_clinic_b_feasibility_is_noised_and_charged(tmp_path):
    g = gate(tmp_path)
    exact = handle_clinic_a(FEAS | {"template": "feasibility_local"}, B)  # B's data, exact
    r = handle_clinic_b(FEAS, B, g)
    assert r["status"] == "ok" and r["eligible_n"] >= 20
    assert abs(r["eligible_n"] - exact["eligible_n"]) < 30
    assert r["budget_remaining"] < 1.0 and "eligible_mrns" not in r


def test_cohort_rate_pct_derived_from_released_counts(tmp_path):
    r = handle_clinic_b(COHORT, B, gate(tmp_path))
    for row in r["results"]:
        assert row["rate_pct"] == round(100 * row["events"] / row["n"], 1)
        assert row["events"] <= row["n"]
    a = handle_clinic_a(COHORT, A)
    assert all(x["rate_pct"] == round(100 * x["events"] / x["n"], 1) for x in a["results"])


def test_clinic_b_suppresses_small_cells(tmp_path):
    q = COHORT | {"filters": {"age_band": "80-89", "diagnosis": "T1D", "sex": "MALE"}}
    r = handle_clinic_b(q, B, gate(tmp_path))
    assert r["status"] == "suppressed" and r["reason"] == "below_disclosure_threshold"
    assert r["results"] == []
    assert MIN_CELL == 10


def test_clinic_b_counter_offers_fine_age_bands(tmp_path):
    q = COHORT | {"filters": {"age_band": "53-57"}}
    r = handle_clinic_b(q, B, gate(tmp_path))
    assert r["status"] == "counter_offer" and r["counter_offer"] == {"age_band": "50-59"}


def test_clinic_b_budget_exhausts_and_persists(tmp_path):
    statuses = [handle_clinic_b(FEAS, B, gate(tmp_path, i))["status"] for i in range(12)]
    assert statuses[:10] == ["ok"] * 10 and statuses[10:] == ["suppressed"] * 2
    r = handle_clinic_b(FEAS, B, gate(tmp_path, 99))   # fresh gate, same ledger file
    assert r["reason"] == "privacy_budget_exhausted"


@pytest.mark.parametrize("bad", [
    FEAS | {"criteria": [{"field": "name", "op": "eq", "value": "x", "checkable": True}]},
    FEAS | {"criteria": [{"field": "age", "op": "has", "value": 1, "checkable": True}]},
    COHORT | {"filters": {"mrn": "B-100001"}},
    COHORT | {"cohorts": ["not_a_drug"]},
    {"template": "select_all"},
])
def test_clinic_b_rejects_out_of_template_requests(tmp_path, bad):
    r = handle_clinic_b(bad, B, gate(tmp_path))
    assert r["status"] == "rejected"


def test_no_canary_leaves_clinic_b(tmp_path):
    g = gate(tmp_path)
    out = json.dumps([handle_clinic_b(q, B, g) for q in
                      (FEAS, COHORT, COHORT | {"filters": {"age_band": "50-59"}})])
    for c in CANARIES["B"] + CANARIES["A"]:
        assert c["name"] not in out and c["mrn"] not in out and c["dob"] not in out


@pytest.mark.parametrize("clinic,req,check", [
    ("a", {"template": "patient_checklist", "mrn": "A-MRN-0042871", "nct_id": "NCT07060456"},
     lambda r: r["summary"]["met"] == 5),
    ("b", FEAS, lambda r: r["status"] == "ok"),
])
def test_agentapp_main_end_to_end(clinic, req, check, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLINIC_B_STATE", str(tmp_path / "b.json"))
    mod = importlib.import_module(f"clinic_{clinic}.agent_app")

    class Events:
        def __init__(self): self.sent = []
        def emit(self, e): self.sent.append(e)

    class Agent:
        prompt = "Coordinator request: " + json.dumps(req)
        events = Events()

    mod.main(Agent(), None)
    printed = json.loads(capsys.readouterr().out)
    assert check(printed) and printed["clinic"] == clinic.upper()
    assert Agent.events.sent[0]["type"] == "response.output_text.delta"
