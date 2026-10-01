"""Run the FL app, collect its result, compare against baselines, plot, and canary-scan.

    # Simulation (2 simulated clinic nodes)
    PYTHONUTF8=1 .venv/Scripts/python.exe run_experiment.py simulation

    # Local deployment (local SuperLink + clinic SuperNodes started by scripts/start_local_grid.sh)
    PYTHONUTF8=1 .venv/Scripts/python.exe run_experiment.py local \
        --clinic-node-ids '{"clinic_a":"<id>","clinic_b":"<id>"}'

Writes results/fl_results.json and docs/media/fl-readmission-*.png, and exits nonzero on
any canary hit or if a non-clinic node was addressed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

APP = Path(__file__).resolve().parent
ROOT = APP.parent
DATA_DIR = ROOT / "clinic-agents"
LOG_DIR = ROOT / "runtime-logs"
RESULTS = APP / "results" / "fl_results.json"
MEDIA = ROOT / "docs" / "media"
CANARIES = DATA_DIR / "data" / "canaries.json"
RESULT_PREFIX = "FL_RESULT "
RECORD_KEYS = ("mrn", "dob", "name", "patients")

sys.path.insert(0, str(APP))
from fl_readmission.evaluate import compare, patient_file, plot_comparison, plot_rounds  # noqa: E402
from fl_readmission.task import CLINIC_ROLES  # noqa: E402


def flwr_command(mode: str, superlink: str | None, clinic_node_ids: str | None) -> list[str]:
    flwr = str(APP / ".venv" / "Scripts" / "flwr.exe")
    if mode == "simulation":
        data_dir = DATA_DIR.as_posix()
        return [flwr, "run", ".", "local", "--stream", "--federation-config", "num-supernodes=2",
                "--run-config", f"simulation=true sim-data-dir='{data_dir}'"]
    return [flwr, "run", ".", superlink or "local-agent", "--stream",
            "--run-config", f"clinic-node-ids='{clinic_node_ids}'"]


def run_flwr(command: list[str], log_path: Path) -> str:
    env = {**os.environ, "PYTHONUTF8": "1"}
    completed = subprocess.run(command, cwd=APP, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = completed.stdout + completed.stderr
    log_path.write_text(output, encoding="utf-8")
    if completed.returncode != 0:
        raise SystemExit(f"flwr run failed ({completed.returncode}); see {log_path}")
    return output


def parse_result(output: str) -> dict[str, Any]:
    lines = [line.split(RESULT_PREFIX, 1)[1] for line in output.splitlines() if RESULT_PREFIX in line]
    if len(lines) != 1:
        raise SystemExit(f"expected exactly one {RESULT_PREFIX.strip()} line, found {len(lines)}")
    return json.loads(re.sub(r"\x1b\[[0-9;]*m", "", lines[0]).strip())


def superlink_reply_sources(log_dir: Path) -> dict[str, int]:
    """Count replies per source node as the SuperLink logged them (deployment only)."""
    text = re.sub(r"\[[0-9;]*m", "", (log_dir / "superlink.err.log").read_text(encoding="utf-8", errors="ignore"))
    counts: dict[str, int] = {}
    for node_id in re.findall(r"Push replies from node_id=(\d+)", text):
        counts[node_id] = counts.get(node_id, 0) + 1
    return counts


def identifier_needles() -> list[str]:
    """Every patient's MRN and name at both clinics, plus all canary DOB formats."""
    needles: set[str] = set()
    for role in CLINIC_ROLES:
        for patient in json.loads(patient_file(DATA_DIR, role).read_text(encoding="utf-8"))["patients"]:
            needles.update({patient["mrn"], patient["name"]})
    for records in json.loads(CANARIES.read_text(encoding="utf-8")).values():
        for record in records:
            year, month, day = record["dob"].split("-")
            needles.update({record["dob"], f"{month}/{day}/{year}"})
            first, last = record["name"].split()[0], record["name"].split()[-1]
            needles.add(f"{last}, {first}")
    return sorted(needles)


def canary_scan(texts: dict[str, str], structured: dict[str, str]) -> dict[str, Any]:
    needles = identifier_needles()
    hits = []
    for label, text in {**texts, **structured}.items():
        lowered = text.lower()
        hits += [{"source": label, "needle_kind": "identifier"} for n in needles if n.lower() in lowered]
    for label, text in structured.items():
        hits += [{"source": label, "needle_kind": f"record key {key}"} for key in RECORD_KEYS if f'"{key}"' in text]
    return {"sources": sorted({**texts, **structured}), "identifiers_checked": len(needles), "hits": len(hits), "hit_details": hits}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["simulation", "local"])
    parser.add_argument("--superlink", default="local-agent")
    parser.add_argument("--clinic-node-ids", help='JSON, e.g. {"clinic_a":"123","clinic_b":"456"} (local mode)')
    parser.add_argument("--from-log", type=Path, help="reuse an existing flwr run log instead of running again")
    args = parser.parse_args()
    if args.mode == "local" and not args.clinic_node_ids and not args.from_log:
        parser.error("local mode needs --clinic-node-ids")

    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"fl_{args.mode}.log"
    if args.from_log:
        output = args.from_log.read_text(encoding="utf-8")
    else:
        output = run_flwr(flwr_command(args.mode, args.superlink, args.clinic_node_ids), log_path)
    fl = parse_result(output)

    results = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {"runs": {}}
    results["runs"]["local_deployment" if args.mode == "local" else "simulation"] = fl
    comparison = compare(fl, DATA_DIR)
    results["comparison"] = {"source_run": args.mode, **comparison}
    runs = list(results["runs"].values())
    if len(runs) > 1:
        results["runs_identical_weights"] = all(
            np.allclose(r["coefficients"], runs[0]["coefficients"], atol=1e-9) and abs(r["intercept"] - runs[0]["intercept"]) < 1e-9
            for r in runs)

    # Nothing but the pinned (or, in simulation, the two) clinic nodes may be addressed.
    addressed_ok = set(fl["addressed_node_ids"]) == set(fl["clinic_node_roles"]) and \
        sorted(fl["clinic_node_roles"].values()) == sorted(CLINIC_ROLES)

    if args.mode == "local":
        # Independent of the ServerApp's own bookkeeping: what the SuperLink saw come back.
        sources = superlink_reply_sources(LOG_DIR)
        expected_replies = 2 * len(fl["rounds"])  # one train + one evaluate reply per round
        fl["superlink_reply_sources"] = sources
        addressed_ok = addressed_ok and set(sources) == set(fl["clinic_node_roles"]) and             all(count == expected_replies for count in sources.values())

    MEDIA.mkdir(parents=True, exist_ok=True)
    plot_rounds(fl, comparison, MEDIA / "fl-readmission-auc-per-round.png")
    plot_comparison(comparison, MEDIA / "fl-readmission-comparison.png")

    texts = {log_path.name: output}
    if args.mode == "local":
        texts |= {p.name: p.read_text(encoding="utf-8", errors="ignore") for p in sorted(LOG_DIR.glob("*.log")) if p != log_path}
    RESULTS.parent.mkdir(exist_ok=True)
    draft = json.dumps(results, indent=2)
    scan = canary_scan(texts, {"fl_results.json": draft, "message_log": json.dumps(fl["message_log"])})
    results.setdefault("canary_scans", {})[args.mode] = scan
    RESULTS.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    models = comparison["models"]
    print(f"rounds: {len(fl['rounds'])}; final weighted AUC {fl['rounds'][-1]['weighted_auc']:.4f}")
    for name, model in models.items():
        s = model["scores"]
        print(f"  {model['label']:<22} AUC A {s['clinic_a']['auc']:.3f}  B {s['clinic_b']['auc']:.3f}  combined {s['combined']['auc']:.3f}"
              f" | log loss combined {s['combined']['log_loss']:.4f} | sglt2 coef {model['sglt2']['coefficient']:+.3f}"
              f" OR {model['sglt2']['odds_ratio']:.2f}")
    print(f"addressed nodes: {fl['addressed_node_ids']} roles {fl['clinic_node_roles']} -> {'ok' if addressed_ok else 'VIOLATION'}")
    if "superlink_reply_sources" in fl:
        print(f"SuperLink reply sources: {fl['superlink_reply_sources']}")
    print(f"canary hits: {scan['hits']} (checked {scan['identifiers_checked']} identifiers across {len(scan['sources'])} sources)")
    return 0 if scan["hits"] == 0 and addressed_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
