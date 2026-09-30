"""Run site feasibility locally against both clinics (no Grid needed).

    python scripts/run_feasibility.py [criteria.json] [--seed N] [--exact]

Default criteria: ../research-agent/cache/criteria_NCT07060456.json (M3's file).
Uses throwaway budget ledgers, so it never spends the real clinic budgets.
--exact also prints the true counts. Those are for local checking only and must
not go on a slide or into a message.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clinic_core.criteria import is_potentially_eligible  # noqa: E402
from clinic_core.feasibility import feasibility_count  # noqa: E402
from clinic_core.privacy import BudgetLedger, PrivacyGate  # noqa: E402
from clinic_core.store import load_patients  # noqa: E402

DEFAULT = ROOT.parent / "research-agent" / "cache" / "criteria_NCT07060456.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("criteria", nargs="?", default=str(DEFAULT))
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--exact", action="store_true")
    args = ap.parse_args()

    doc = json.loads(Path(args.criteria).read_text())
    criteria, nct_id = doc["criteria"], doc["trial"]["nct_id"]
    tmp = Path(tempfile.mkdtemp())
    for clinic in ("A", "B"):
        patients = load_patients(clinic, ROOT / f"clinic-{clinic.lower()}-agent" / f"clinic_{clinic.lower()}" / "data")
        rng = random.Random(args.seed) if args.seed is not None else random.SystemRandom()
        gate = PrivacyGate(BudgetLedger.create_session(tmp / f"{clinic}.json"), rng)
        out = feasibility_count(clinic, nct_id, criteria, patients, gate)
        print(json.dumps(out))
        if args.exact:
            print(f"  [local only] exact count = {sum(is_potentially_eligible(criteria, p) for p in patients)}")


if __name__ == "__main__":
    main()
