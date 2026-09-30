from __future__ import annotations

import json
import logging
import sys
import types
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "coordinator"),
    str(ROOT / "doctor-agent"),
    str(ROOT / "clinic-agents"),
    str(ROOT / "clinic-agents" / "clinic-a-agent"),
]

from cohortguard_coordinator.agent_app import main
from cohortguard_coordinator.grid import MAX_GRID_TIMEOUT
from cohortguard_coordinator.nodes import _research_response
from cohortguard_coordinator.summary import summary_instructions, write_final_summary
from doctor_agent.core import LocalContext, local_patient_answer, prepare_coordinator_request

ROLES = {"101": "clinic_a", "202": "clinic_b", "303": "research"}


class Events:
    def __init__(self):
        self.sent = []

    def emit(self, event):
        self.sent.append(event)


class FakeGrid:
    def __init__(self, tools=None):
        self._tools = tools or ["get_nodes", "push_messages", "pull_messages"]
        self.calls = []
        self.pending = {}
        self.counter = 0

    def tools(self):
        return [{"name": name} for name in self._tools]

    def call(self, item):
        name = item["name"]
        args = json.loads(item["arguments"])
        self.calls.append((name, args))
        logging.getLogger("grid-test").debug("%s %s", name, json.dumps(args))
        if name == "get_nodes":
            output = {"nodes": [{"id": node, "name": None, "location": None} for node in ROLES], "num_available": 3}
        elif name == "push_messages":
            results = []
            for message in args["messages"]:
                self.counter += 1
                message_id = f"msg-{self.counter}"
                self.pending[message_id] = (message["dst_node_id"], json.loads(message["payload"]))
                results.append({"message_id": message_id, "error": None})
            output = {"results": results}
        elif name == "pull_messages":
            messages = []
            for message_id in args["message_ids"]:
                node_id, request = self.pending.pop(message_id)
                role = ROLES[node_id]
                payload = self._reply(role, request)
                messages.append({
                    "message_id": f"reply-{message_id}",
                    "reply_to_message_id": message_id,
                    "src_node_id": node_id,
                    "payload": json.dumps(payload),
                    "error": None,
                })
            output = {"messages": messages, "pending_message_ids": []}
        elif name == "push_reply_message":
            output = {"message_id": "reply-id", "error": None}
        else:
            raise AssertionError(name)
        return {"type": "function_call_output", "call_id": item["call_id"], "output": json.dumps(output)}

    @staticmethod
    def _reply(role, request):
        if request.get("type") == "identify_role":
            return {"type": "role_identity", "role": role}
        if role == "research" and request.get("type") == "pipeline":
            return {"type": "pipeline", "candidates": [], "blocked": [], "grounding_log": []}
        if role == "research" and request.get("type") == "trial_criteria":
            return {"type": "trial_criteria", "trial": {"nct_id": request["nct_id"]}, "criteria": []}
        if request.get("template") == "feasibility_count":
            return {"clinic": "A" if role == "clinic_a" else "B", "status": "ok", "eligible_n": 20, "unchecked_criteria": []}
        return {
            "clinic": "A" if role == "clinic_a" else "B",
            "status": "ok",
            "results": [{"cohort": cohort, "n": 20, "events": 2, "rate_pct": 10.0} for cohort in request["cohorts"]],
        }


class Agent:
    def __init__(self, prompt, grid):
        self.prompt = prompt
        self.grid = grid
        self.events = Events()


def context(run_config=None, node_config=None):
    return SimpleNamespace(run_config=run_config or {}, node_config=node_config or {}, node_id=1)


def base_request(workflow):
    return {"sanitized": True, "request_id": "req-1", "question": "deidentified question", "workflow": workflow}


@pytest.mark.parametrize("workflow_request", [
    base_request("cohort_question") | {"cohort_field": "medication", "cohorts": ["sglt2_inhibitor", "sulfonylurea"], "outcome": "readmit_30d", "filters": {"diagnosis": "T2D"}},
    base_request("trial_pipeline") | {"condition": "type 2 diabetes", "outcome_keywords": ["readmission"]},
    base_request("site_feasibility") | {"nct_id": "NCT07060456"},
])
def test_each_workflow_is_code_driven(workflow_request):
    grid = FakeGrid()
    agent = Agent(json.dumps(workflow_request), grid)
    main(agent, context({"model": "", "grid_timeout": 10.0, "expected_role_node_ids": "{}"}))
    text = "".join(event.get("delta", "") for event in agent.events.sent)
    result = json.loads(text)
    assert result["workflow"] == workflow_request["workflow"]
    assert [name for name, _ in grid.calls].count("get_nodes") == 1
    assert all(name in {"get_nodes", "push_messages", "pull_messages"} for name, _ in grid.calls)


def test_cohort_result_includes_code_computed_rate_difference_ci():
    grid = FakeGrid()
    request = base_request("cohort_question") | {
        "cohort_field": "medication",
        "cohorts": ["sglt2_inhibitor", "sulfonylurea"],
        "outcome": "readmit_30d",
        "filters": {"diagnosis": "T2D"},
    }
    agent = Agent(json.dumps(request), grid)
    main(agent, context({"model": "", "grid_timeout": 10.0, "expected_role_node_ids": "{}"}))
    result = json.loads("".join(event.get("delta", "") for event in agent.events.sent))
    assert result["rate_difference"] == {
        "cohort_a": "sglt2_inhibitor",
        "cohort_b": "sulfonylurea",
        "difference_pct_points": 0.0,
        "ci_95_pct_points": [-13.1, 13.1],
        "method": "wald_difference_in_proportions_on_released_counts",
    }


def test_summary_instructions_are_workflow_specific():
    cohort = summary_instructions("cohort_question")
    site = summary_instructions("site_feasibility")
    trial = summary_instructions("trial_pipeline")
    assert "noised cohort sizes" in cohort and "observational" in cohort and "confidence interval" in cohort
    assert "potentially eligible upper-bound" in site
    assert "Do not add differential-privacy or patient-count caveats" in trial
    forbidden = "patient-level facts were or were not accessed"
    assert forbidden not in cohort and forbidden not in site and forbidden not in trial


def test_final_summary_uses_fake_model_and_workflow_instructions(monkeypatch):
    calls = []

    class Responses:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return {"output_text": "fake summary"}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = Responses()

    monkeypatch.setenv("FLWR_RUNTIME_BASE_URL", "http://runtime.test")
    monkeypatch.setenv("FLWR_RUNTIME_API_KEY", "token")
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    text = write_final_summary(
        {"question": "deidentified", "workflow": "cohort_question"},
        {"workflow": "cohort_question", "rate_difference": {"ci_95_pct_points": [-1, 1]}},
        "fake-model",
        "low",
    )
    assert text == "fake summary"
    assert calls[0]["model"] == "fake-model"
    assert calls[0]["reasoning"] == {"effort": "low"}
    assert "noised cohort sizes" in calls[0]["instructions"]
    assert "confidence interval" in calls[0]["instructions"]


def test_expected_role_mapping_rejects_mismatched_claim():
    grid = FakeGrid()
    request = base_request("trial_pipeline") | {"condition": "type 2 diabetes", "outcome_keywords": []}
    agent = Agent(json.dumps(request), grid)
    expected = json.dumps({"clinic_a": "202", "clinic_b": "101", "research": "303"})
    main(agent, context({"model": "", "grid_timeout": 10.0, "expected_role_node_ids": expected}))
    assert json.loads(agent.events.sent[0]["delta"])["error"] == "workflow_failed"


def test_coordinator_rejects_identifier_before_grid_call():
    grid = FakeGrid()
    request = base_request("trial_pipeline") | {
        "question": "Maria Delgado MRN A-MRN-0042871 born 1970-01-02",
        "condition": "type 2 diabetes",
        "outcome_keywords": [],
    }
    agent = Agent(json.dumps(request), grid)
    main(agent, context({"model": ""}))
    assert grid.calls == []
    assert json.loads(agent.events.sent[0]["delta"])["error"] == "request_rejected"


def test_doctor_scrubbing_leaves_zero_canaries_in_all_boundaries(caplog):
    patients = ROOT / "clinic-agents/clinic-a-agent/clinic_a/data/clinic_a_patients.json"
    canary_path = ROOT / "clinic-agents/data/canaries.json"
    local = LocalContext.load(patients, canary_path)
    maria = next(patient for patient in local.patients if patient["name"] == "Maria Delgado")
    iso_dob = maria["dob"]
    year, month, day = iso_dob.split("-")
    slash_dob = f"{month}/{day}/{year}"
    month_name = "April" if month == "04" else month
    long_dob = f"{month_name} {int(day)}, {year}"
    reversed_name = "Delgado, Maria"
    question = (
        f"For {maria['name']} also written {reversed_name}, with MRN {maria['mrn']} "
        f"and DOBs {iso_dob}, {slash_dob}, and {long_dob}, "
        "compare the readmission cohort for SGLT2 and sulfonylurea in type 2 diabetes."
    )
    request = prepare_coordinator_request(question, local)
    grid = FakeGrid()
    agent = Agent(json.dumps(request), grid)
    with caplog.at_level(logging.DEBUG, logger="grid-test"):
        main(agent, context({"model": "", "grid_timeout": 10.0, "expected_role_node_ids": "{}"}))
    boundaries = json.dumps({
        "coordinator_prompt": agent.prompt,
        "grid_calls": grid.calls,
        "events": agent.events.sent,
        "logs": caplog.messages,
    })
    lowered_boundaries = boundaries.lower()
    for forbidden in (maria["name"], reversed_name, maria["mrn"], iso_dob, slash_dob, long_dob):
        assert forbidden.lower() not in lowered_boundaries
    canaries = json.loads(canary_path.read_text(encoding="utf-8"))
    for records in canaries.values():
        for canary in records:
            for key in ("name", "mrn", "dob"):
                assert canary[key].lower() not in lowered_boundaries


def test_local_patient_answer_never_prints(capsys):
    local = LocalContext.load(
        ROOT / "clinic-agents/clinic-a-agent/clinic_a/data/clinic_a_patients.json",
        ROOT / "clinic-agents/data/canaries.json",
    )
    maria = next(patient for patient in local.patients if patient["name"] == "Maria Delgado")
    result = local_patient_answer(
        f"Is {maria['name']} MRN {maria['mrn']} eligible for NCT07060456?", local
    )
    assert result and result["mrn"] == maria["mrn"]
    assert capsys.readouterr().out == ""


def test_supernode_replies_exactly_once_without_print_or_extra_events(capsys):
    grid = FakeGrid(["push_reply_message"])
    envelope = {"message_id": "m1", "src_node_id": "1", "payload": json.dumps({"type": "identify_role"})}
    agent = Agent(json.dumps(envelope), grid)
    main(agent, context(node_config={"role": "research"}))
    assert [name for name, _ in grid.calls] == ["push_reply_message"]
    assert agent.events.sent == []
    assert capsys.readouterr().out == ""


def test_research_web_page_log_is_removed(monkeypatch):
    from cohortguard_coordinator import nodes

    monkeypatch.setattr(nodes, "safe_handle_request", lambda *_args, **_kw: {
        "type": "web_page", "url": "https://example.test", "status": 200,
        "summary": "safe", "injection_detected": True, "log": [{"secret": "do not forward"}],
    })
    result = _research_response({"type": "web_page"}, context({"with_mechanism": False}), SimpleNamespace())
    assert result == {
        "type": "web_page", "url": "https://example.test", "status": 200,
        "summary": "safe", "injection_detected": True,
    }


def test_capitalized_clinical_terms_drugs_trial_ids_and_clinic_labels_pass_identifier_check():
    grid = FakeGrid()
    request = base_request("site_feasibility") | {
        "question": (
            "For Clinic A and Clinic B, estimate feasibility for NCT07060456 in Type 2 Diabetes, "
            "Chronic Kidney Disease, Heart Failure, SGLT2, GLP-1, DPP-4, Metformin, and Basal Insulin cohorts."
        ),
        "nct_id": "NCT07060456",
    }
    agent = Agent(json.dumps(request), grid)
    main(agent, context({"model": "", "grid_timeout": 10.0, "expected_role_node_ids": "{}"}))
    text = "".join(event.get("delta", "") for event in agent.events.sent)
    assert json.loads(text)["workflow"] == "site_feasibility"
    assert grid.calls


def test_hashed_exact_canary_rejected_before_grid_call():
    grid = FakeGrid()
    request = base_request("trial_pipeline") | {
        "question": "Evaluate rare record A-9101 for trial matching",
        "condition": "type 2 diabetes",
        "outcome_keywords": [],
    }
    agent = Agent(json.dumps(request), grid)
    main(agent, context({"model": ""}))
    assert grid.calls == []
    assert json.loads(agent.events.sent[0]["delta"])["error"] == "request_rejected"


def test_coordinator_fab_contains_no_raw_canary_values():
    canaries = json.loads((ROOT / "clinic-agents/data/canaries.json").read_text(encoding="utf-8"))
    raw_values = {
        str(record[key]).encode("utf-8")
        for records in canaries.values()
        for record in records
        for key in ("name", "mrn", "dob")
    }
    fab_paths = list((ROOT / "coordinator").glob("*.fab"))
    assert fab_paths, "Build a coordinator FAB before running this assertion."
    for fab_path in fab_paths:
        with zipfile.ZipFile(fab_path) as archive:
            for name in archive.namelist():
                data = archive.read(name)
                assert not any(raw in data for raw in raw_values), f"raw canary found in {fab_path}:{name}"


def test_grid_timeout_cap_stays_well_below_task_limit():
    assert MAX_GRID_TIMEOUT == 60.0
