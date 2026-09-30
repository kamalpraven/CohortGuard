"""Explicit local-only access to Clinic A patient-level workflows.

This process does not use Flower or a SuperLink. Exact output is enabled only
when the operator supplies ``--allow-exact-local``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "clinic-a-agent")]

from clinic_a.clinic import handle_clinic_a  # noqa: E402
from clinic_core.store import load_patients  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-exact-local", action="store_true", help="acknowledge local exact output")
    parser.add_argument("request", help="a complete JSON request object")
    args = parser.parse_args()
    if not args.allow_exact_local:
        parser.error("exact local mode requires --allow-exact-local")
    try:
        request = json.loads(args.request)
    except json.JSONDecodeError:
        print(json.dumps({"status": "rejected", "reason": "invalid_request"}))
        return 2
    if not isinstance(request, dict):
        print(json.dumps({"status": "rejected", "reason": "invalid_request"}))
        return 2
    try:
        patients = load_patients("A", ROOT / "clinic-a-agent" / "clinic_a" / "data")
        result = handle_clinic_a(request, patients)
    except Exception:
        result = {"clinic": "A", "status": "error", "reason": "internal_error"}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
