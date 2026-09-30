"""Run from clinic-agents after ``python ../scripts/sync_apps.py``: pytest tests."""

import importlib
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Repo root first so tests use the source clinic_core, not the vendored copies.
sys.path[:0] = [str(ROOT), str(ROOT / "clinic-a-agent"), str(ROOT / "clinic-b-agent")]

from clinic_a.clinic import handle_clinic_a  # noqa: E402
from clinic_b.clinic import handle_clinic_b  # noqa: E402
from clinic_core.privacy import MIN_CELL, BudgetLedger, PrivacyGate  # noqa: E402
from clinic_core.store import load_patients  # noqa: E402

DATA = {c: ROOT / f"clinic-{c.lower()}-agent" / f"clinic_{c.lower()}" / "data" for c in "AB"}
A = load_patients("A", DATA["A"])
B = load_patients("B", DATA["B"])
CANARIES = json.loads((ROOT / "data" / "canaries.json").read_text())
FEAS = {"template": "feasibility_count", "nct_id": "NCT07060456"}
COHORT = {"template": "outcome_rate_by_cohort", "cohort_field": "medication",
          "cohorts": ["sglt2_inhibitor", "sulfonylurea"], "outcome": "readmit_30d",
          "filters": {"diagnosis": "T2D", "age_band": "50-59"}}


def test_generated_app_copies_match_canonical_sources():
    script = ROOT.parent / "scripts" / "sync_apps.py"
    result = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


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


def test_clinic_b_feasibility_fixed_seed_regression(tmp_path):
    """Pin Clinic B's released values while cleanup refactors its request path."""
    r = handle_clinic_b(FEAS, B, gate(tmp_path, seed=1))
    assert {k: r[k] for k in ("eligible_n", "noise_scale", "budget_remaining")} == {
        "eligible_n": 81,
        "noise_scale": 2.0,
        "budget_remaining": 0.9,
    }
    assert r["status"] == "ok" and len(r["unchecked_criteria"]) == 8


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


def test_reflection_channels_do_not_echo_canaries(tmp_path):
    canary = CANARIES["A"][0]["name"]
    responses = [
        handle_clinic_b(FEAS | {"request_id": canary}, B, gate(tmp_path / "id")),
        handle_clinic_b({"template": canary, "request_id": "safe-id"}, B, gate(tmp_path / "template")),
        handle_clinic_b(
            FEAS | {"criteria": [{"field": "unmapped", "checkable": False, "source_text": canary}]},
            B,
            gate(tmp_path / "criteria"),
        ),
    ]
    assert canary not in json.dumps(responses)
    assert responses[0]["request_id"] is None and responses[0]["reason"] == "invalid_request"
    assert responses[1]["request_id"] == "safe-id" and responses[1]["reason"] == "invalid_request"
    assert responses[2]["unchecked_criteria"] == ["criterion_1"]


@pytest.mark.parametrize("clinic,req,check", [
    ("a", FEAS, lambda r: r["status"] == "ok" and "eligible_n" in r),
    ("b", FEAS, lambda r: r["status"] == "ok"),
])
def test_agentapp_main_end_to_end(clinic, req, check, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLINIC_B_STATE", str(tmp_path / "b.json"))
    mod = importlib.import_module(f"clinic_{clinic}.agent_app")

    class Events:
        def __init__(self): self.sent = []
        def emit(self, e): self.sent.append(e)

    class Agent:
        prompt = json.dumps(req)
        events = Events()

    mod.main(Agent(), None)
    printed = json.loads(capsys.readouterr().out)
    assert check(printed) and printed["clinic"] == clinic.upper()
    assert Agent.events.sent[0]["type"] == "response.output_text.delta"


def test_clinic_a_deployment_never_emits_exact_patient_result(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLINIC_A_STATE", str(tmp_path / "a.json"))
    from clinic_a import agent_app

    class Events:
        def __init__(self): self.sent = []
        def emit(self, event): self.sent.append(event)

    class Agent:
        prompt = json.dumps({
            "template": "patient_checklist",
            "name": CANARIES["A"][0]["name"],
            "nct_id": "NCT07060456",
        })
        events = Events()

    agent_app.main(Agent(), None)
    printed = capsys.readouterr().out
    emitted = json.dumps(Agent.events.sent)
    assert json.loads(printed)["status"] == "rejected"
    for canary in CANARIES["A"] + CANARIES["B"]:
        assert canary["name"] not in printed + emitted
        assert canary["mrn"] not in printed + emitted
        assert canary["dob"] not in printed + emitted


@pytest.mark.parametrize("prompt", ["find patients like Maria", "prefix {\"template\": \"none\"}", "[]", "{bad"])
def test_agentapp_rejects_non_object_or_free_text_without_model(prompt, capsys):
    from clinic_b import agent_app

    class Events:
        def __init__(self): self.sent = []
        def emit(self, event): self.sent.append(event)

    class Agent:
        events = Events()

    Agent.prompt = prompt
    agent_app.main(Agent(), None)
    result = json.loads(capsys.readouterr().out)
    assert result == {"clinic": "B", "status": "rejected", "reason": "no_valid_request"}


RESEARCH_CRITERIA = ROOT.parent / "research-agent" / "cache" / "criteria_NCT07060456.json"


@pytest.mark.parametrize("clinic,patients", [("A", A), ("B", B)])
def test_feasibility_spec_output_both_clinics(clinic, patients, tmp_path):
    from clinic_core.feasibility import feasibility_count
    from clinic_core.criteria import is_potentially_eligible
    doc = json.loads((RESEARCH_CRITERIA if RESEARCH_CRITERIA.exists() else
                      ROOT / "cache" / "criteria_NCT07060456.json").read_text())
    out = feasibility_count(clinic, "NCT07060456", doc["criteria"], patients, gate(tmp_path))
    assert {"nct_id", "clinic", "eligible_n", "unchecked_criteria", "status"} <= set(out)
    assert out["clinic"] == clinic and out["status"] == "ok" and len(out["unchecked_criteria"]) == 8
    exact = sum(is_potentially_eligible(doc["criteria"], p) for p in patients)
    assert exact >= 20 and abs(out["eligible_n"] - exact) < 30
    maria = next((p for p in patients if p["name"] == "Maria Delgado"), None)
    assert maria is None or not is_potentially_eligible(doc["criteria"], maria)


def test_clinic_a_gated_feasibility_hides_identifiers(tmp_path):
    r = handle_clinic_a(FEAS, A, gate(tmp_path))
    assert r["status"] == "ok" and r["scope"] == "gated" and "eligible_mrns" not in r
    assert handle_clinic_a(FEAS, A)["status"] == "rejected"   # no gate, no count
