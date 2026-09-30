from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from flwr.cli.chat.chat_app import parse_task_event, start_chat_run
from flwr.cli.chat.chat_local_agent import build_local_agent
from flwr.cli.constant import CHAT_FAILURE_EVENTS, CHAT_TERMINAL_EVENTS, CHAT_TEXT_DELTA_EVENT
from flwr.cli.flower_config import read_superlink_connection
from flwr.cli.utils import init_http_client_from_connection
from flwr.proto.control_pb2 import StreamRunEventsRequest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "doctor-agent"),
    str(ROOT / "coordinator"),
    str(ROOT / "clinic-agents"),
    str(ROOT / "clinic-agents" / "clinic-a-agent"),
]

from doctor_agent.core import LocalContext, prepare_coordinator_request  # noqa: E402

PATIENTS = ROOT / "clinic-agents/clinic-a-agent/clinic_a/data/clinic_a_patients.json"
CANARIES = ROOT / "clinic-agents/data/canaries.json"
COORDINATOR = ROOT / "coordinator"
PYPROJECT = COORDINATOR / "pyproject.toml"


def _toml_string(value: str) -> str:
    return json.dumps(value)


def patch_run_config(*, expected_role_node_ids: str, model: str) -> str:
    original = PYPROJECT.read_text(encoding="utf-8")
    updated = re.sub(r'^model = .*$', f"model = {_toml_string(model)}", original, flags=re.MULTILINE)
    updated = re.sub(r'^with_mechanism = .*$', "with_mechanism = false", updated, flags=re.MULTILINE)
    updated = re.sub(
        r'^expected_role_node_ids = .*$',
        f"expected_role_node_ids = {_toml_string(expected_role_node_ids)}",
        updated,
        flags=re.MULTILINE,
    )
    PYPROJECT.write_text(updated, encoding="utf-8")
    return original


def submit_and_collect(
    request: dict[str, Any], *, superlink: str, federation: str | None
) -> tuple[int, str, list[str]]:
    prompt = json.dumps(request, separators=(",", ":"), ensure_ascii=False)
    local_agent = build_local_agent(COORDINATOR)
    connection = read_superlink_connection(superlink)
    client = init_http_client_from_connection(connection)
    events: list[str] = []
    try:
        run_id, _series_id = start_chat_run(
            client,
            prompt,
            federation,
            None,
            local_agent.app_spec,
            local_agent.fab_hash,
            local_agent.fab_content,
        )
        deltas: list[str] = []
        for response in client.StreamRunEvents(StreamRunEventsRequest(run_id=run_id)):
            raw_event = str(response.task_event)
            events.append(raw_event)
            event_type, payload = parse_task_event(response.task_event)
            events.append(json.dumps({"event_type": event_type, "payload": payload}, ensure_ascii=False, default=str))
            if event_type == CHAT_TEXT_DELTA_EVENT and isinstance(payload.get("delta"), str):
                deltas.append(payload["delta"])
            if event_type in CHAT_FAILURE_EVENTS:
                raise RuntimeError(f"Coordinator run {run_id} failed for request {request.get('request_id')}")
            if event_type in CHAT_TERMINAL_EVENTS:
                break
        return run_id, "".join(deltas) or "Coordinator returned no summary.", events
    finally:
        client.close()


def maria_needles(local: LocalContext) -> list[str]:
    maria = next(patient for patient in local.patients if patient["name"] == "Maria Delgado")
    year, month, day = str(maria["dob"]).split("-")
    return [
        str(maria["name"]),
        "Delgado, Maria",
        str(maria["mrn"]),
        str(maria["dob"]),
        f"{month}/{day}/{year}",
        f"April {int(day)}, {year}",
    ]


def scan_texts(texts: list[str], needles: list[str]) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for index, text in enumerate(texts):
        lowered = text.lower()
        for needle in needles:
            if needle.lower() in lowered:
                hits.append((f"event_{index}", needle))
    return hits


def scan_logs(log_dir: Path, needles: list[str]) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for path in sorted(log_dir.glob("*.log")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        lowered = text.lower()
        for needle in needles:
            if needle.lower() in lowered:
                hits.append((str(path), needle))
    return hits


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CohortGuard demo on Flower SuperGrid and scan events/logs.")
    parser.add_argument("--superlink", default="supergrid")
    parser.add_argument("--federation", default=None)
    parser.add_argument("--expected-role-node-ids", required=True, help='JSON mapping, e.g. {"clinic_a":"..."}')
    parser.add_argument("--model", default="", help="SuperGrid model ID for final summaries. Empty disables model calls.")
    parser.add_argument("--allow-model-calls", action="store_true", help="Required when --model is non-empty.")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "runtime-logs")
    args = parser.parse_args()

    expected = json.loads(args.expected_role_node_ids)
    if set(expected) != {"clinic_a", "clinic_b", "research"}:
        raise SystemExit("expected-role-node-ids must map clinic_a, clinic_b, and research")
    expected_json = json.dumps({k: str(v) for k, v in expected.items()}, separators=(",", ":"))

    if args.model and not args.allow_model_calls:
        raise SystemExit("Refusing to make model calls without --allow-model-calls.")

    local = LocalContext.load(PATIENTS, CANARIES)
    maria = next(patient for patient in local.patients if patient["name"] == "Maria Delgado")
    questions = {
        "cohort_question": "Compare readmission cohorts for SGLT2 and sulfonylurea in type 2 diabetes across Clinic A and Clinic B.",
        "trial_pipeline": "Find trials for type 2 diabetes with readmission or hospitalization outcomes.",
        "site_feasibility": "Estimate site feasibility for NCT07060456 in type 2 diabetes across Clinic A and Clinic B.",
        "maria_scrubbed_cohort": (
            f"For {maria['name']} also written Delgado, Maria, MRN {maria['mrn']}, "
            f"DOB {maria['dob']}, compare readmission cohorts for SGLT2 and sulfonylurea in type 2 diabetes."
        ),
    }

    original_pyproject = patch_run_config(expected_role_node_ids=expected_json, model=args.model)
    run_ids: dict[str, int] = {}
    all_event_texts: list[str] = []
    try:
        for label, question in questions.items():
            request = prepare_coordinator_request(question, local)
            print(f"=== {label} request ===")
            print(json.dumps(request, sort_keys=True, ensure_ascii=False))
            run_id, response, events = submit_and_collect(request, superlink=args.superlink, federation=args.federation)
            run_ids[label] = run_id
            all_event_texts.extend(events)
            print(f"=== {label} run_id ===")
            print(run_id)
            print(f"=== {label} response ===")
            print(response)
            parsed = json.loads(response)
            if args.model:
                # Model summaries may be prose; JSON workflow assertion only applies to no-model runs.
                continue
            if parsed.get("workflow") != request["workflow"]:
                raise RuntimeError(f"{label} routed to {parsed.get('workflow')}, expected {request['workflow']}")
    finally:
        PYPROJECT.write_text(original_pyproject, encoding="utf-8")

    needles = maria_needles(local)
    event_hits = scan_texts(all_event_texts, needles)
    log_hits = scan_logs(args.log_dir, needles)
    total_hits = len(event_hits) + len(log_hits)
    print("=== run_ids ===")
    print(json.dumps(run_ids, indent=2, sort_keys=True))
    print(f"canary hits: {total_hits}")
    if total_hits:
        print(json.dumps({"event_hits": event_hits, "log_hits": log_hits}, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
