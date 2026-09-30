from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "doctor-agent"),
    str(ROOT / "coordinator"),
    str(ROOT / "clinic-agents"),
    str(ROOT / "clinic-agents" / "clinic-a-agent"),
]

from cohortguard_coordinator.identifiers import REDACTION
from doctor_agent.core import LocalContext, local_patient_answer, prepare_coordinator_request, scrub_doctor_question

PATIENTS = ROOT / "clinic-agents/clinic-a-agent/clinic_a/data/clinic_a_patients.json"
CANARIES = ROOT / "clinic-agents/data/canaries.json"


def load_local() -> LocalContext:
    return LocalContext.load(PATIENTS, CANARIES)


def test_scrubber_removes_name_variants_mrn_and_date_formats():
    local = load_local()
    cases = [
        "maria delgado needs review",
        "Delgado, Maria needs review",
        "Maria needs review",
        "A-MRN-0042871 needs review",
        "DOB 1968-04-17 needs review",
        "DOB 04/17/1968 needs review",
        "DOB April 17, 1968 needs review",
    ]
    for question in cases:
        scrubbed = scrub_doctor_question(question, local)
        assert REDACTION in scrubbed
        assert "Maria" not in scrubbed
        assert "Delgado" not in scrubbed
        assert "A-MRN-0042871" not in scrubbed
        assert "1968" not in scrubbed
        assert "04/17" not in scrubbed
        assert "April 17" not in scrubbed


def test_scrubber_does_not_redact_clinical_terms_or_drug_names():
    local = load_local()
    question = (
        "Compare SGLT2, GLP-1, DPP-4, metformin, sulfonylurea, and basal insulin "
        "for Type 2 diabetes readmission cohorts with HbA1c and eGFR filters."
    )
    scrubbed = scrub_doctor_question(question, local)
    assert REDACTION not in scrubbed
    for term in ["SGLT2", "GLP-1", "DPP-4", "metformin", "sulfonylurea", "basal insulin", "HbA1c", "eGFR"]:
        assert term in scrubbed


def test_trial_mentions_route_to_trial_pipeline_even_with_readmission_terms():
    local = load_local()
    request = prepare_coordinator_request(
        "Find trials for type 2 diabetes with readmission or hospitalization outcomes.", local
    )
    assert request["workflow"] == "trial_pipeline"


def test_local_patient_answer_never_reaches_stdout_events_or_submitted_payload(capsys):
    local = load_local()
    maria = next(patient for patient in local.patients if patient["name"] == "Maria Delgado")
    question = f"Is {maria['name']} MRN {maria['mrn']} eligible for NCT07060456?"

    submitted_payloads: list[dict] = []
    events: list[dict] = []

    local_result = local_patient_answer(question, local)
    if local_result is None:
        submitted_payloads.append(prepare_coordinator_request(question, local))
        events.append({"type": "submitted"})

    captured = capsys.readouterr()
    boundary = json.dumps({"stdout": captured.out, "events": events, "submitted": submitted_payloads})
    assert local_result is not None
    assert captured.out == ""
    assert submitted_payloads == []
    assert events == []
    assert maria["name"] not in boundary
    assert maria["mrn"] not in boundary
    assert maria["dob"] not in boundary
