from __future__ import annotations

import argparse
import json
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


def submit_and_collect(request: dict[str, Any], *, superlink: str, federation: str | None) -> tuple[str, list[str]]:
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
                raise RuntimeError(f"Coordinator run failed for request {request.get('request_id')}")
            if event_type in CHAT_TERMINAL_EVENTS:
                break
        return "".join(deltas) or "Coordinator returned no summary.", events
    finally:
        client.close()


def scan_texts(texts: list[str], needles: list[str]) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for label, text in zip((f"text_{i}" for i in range(len(texts))), texts):
        lowered = text.lower()
        for needle in needles:
            if needle.lower() in lowered:
                hits.append((label, needle))
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
    parser = argparse.ArgumentParser(description="Run local CohortGuard demo workflows and scan Flower events/logs.")
    parser.add_argument("--superlink", default="local-agent")
    parser.add_argument("--federation", default=None)
    parser.add_argument("--log-dir", type=Path, default=ROOT / "runtime-logs")
    args = parser.parse_args()

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

    all_event_texts: list[str] = []
    for label, question in questions.items():
        request = prepare_coordinator_request(question, local)
        print(f"=== {label} request ===")
        print(json.dumps(request, sort_keys=True, ensure_ascii=False))
        response, events = submit_and_collect(request, superlink=args.superlink, federation=args.federation)
        all_event_texts.extend(events)
        print(f"=== {label} response ===")
        print(response)
        parsed = json.loads(response)
        if parsed.get("workflow") != request["workflow"]:
            raise RuntimeError(f"{label} routed to {parsed.get('workflow')}, expected {request['workflow']}")

    needles = maria_needles(local)
    event_hits = scan_texts(all_event_texts, needles)
    log_hits = scan_logs(args.log_dir, needles)
    total_hits = len(event_hits) + len(log_hits)
    print(f"canary hits: {total_hits}")
    if total_hits:
        print(json.dumps({"event_hits": event_hits, "log_hits": log_hits}, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
