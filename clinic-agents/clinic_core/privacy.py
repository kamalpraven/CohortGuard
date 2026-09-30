"""Clinic B privacy gate: min cell size, Laplace noise, budget ledger.

Deterministic code only. Every count leaving the clinic goes through
`PrivacyGate.release`.

Releases are suppressed if either the true gate count or any noised released
count is below the minimum cell size. Post-noise suppressions still charge the
privacy budget: the query was evaluated and the threshold decision itself is a
release about the noised result, so charging is the safer accounting default.
"""

from __future__ import annotations

import json
import math
import os
import random
import tempfile
from pathlib import Path

MIN_CELL = 10                # raw counts below this are suppressed
EPSILON_PER_COUNT = 0.5      # Laplace scale = sensitivity(1) / epsilon = 2.0
TOTAL_BUDGET = 5.0           # cumulative epsilon per clinic


class BudgetLedger:
    """Cumulative epsilon spend, persisted so a restart cannot reset it."""

    def __init__(self, path: Path, total: float = TOTAL_BUDGET):
        self.path, self.total = Path(path), total

    def _spent(self) -> float:
        try:
            return float(json.loads(self.path.read_text())["spent"])
        except (FileNotFoundError, ValueError, KeyError):
            return 0.0

    def remaining(self) -> float:
        return max(0.0, self.total - self._spent())

    def charge(self, cost: float) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent)
        with os.fdopen(fd, "w") as f:
            json.dump({"spent": self._spent() + cost, "total": self.total}, f)
        os.replace(tmp, self.path)


def laplace(scale: float, rng: random.Random) -> float:
    u = rng.random() - 0.5
    return -scale * math.copysign(1, u) * math.log(1 - 2 * abs(u))


class PrivacyGate:
    def __init__(self, ledger: BudgetLedger, rng: random.Random | None = None):
        self.ledger = ledger
        self.rng = rng or random.SystemRandom()
        self.scale = 1.0 / EPSILON_PER_COUNT

    def release(self, raw_counts: dict[str, int], gate_on: int) -> dict:
        """Gate a set of counts.

        `gate_on` is the raw cohort size the min-cell rule applies to. Returns
        {"suppressed": True, "reason": ...} or {"suppressed": False, "counts":
        {...noised ints...}, "noise_scale": ..., "budget_remaining": ...}.
        Post-noise suppressions are charged before returning suppression.
        """
        if gate_on < MIN_CELL:
            return {"suppressed": True, "reason": "below_disclosure_threshold"}
        cost = EPSILON_PER_COUNT * len(raw_counts)
        if self.ledger.remaining() < cost:
            return {"suppressed": True, "reason": "privacy_budget_exhausted"}
        noised = {
            k: max(0, round(v + laplace(self.scale, self.rng))) for k, v in raw_counts.items()
        }
        self.ledger.charge(cost)
        if any(value < MIN_CELL for value in noised.values()):
            return {"suppressed": True, "reason": "below_disclosure_threshold"}
        return {
            "suppressed": False,
            "counts": noised,
            "noise_scale": self.scale,
            "budget_remaining": round(self.ledger.remaining() / self.ledger.total, 3),
        }
