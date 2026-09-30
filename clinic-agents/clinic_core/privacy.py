"""Clinic B privacy gate: min cell size, Laplace noise, budget ledger.

Deterministic code only. Every count leaving the clinic goes through
`PrivacyGate.release`.

Releases are suppressed if either the true gate count or any noised released
count is below the minimum cell size. Post-noise suppressions still charge the
privacy budget: the query was evaluated and the threshold decision itself is a
release about the noised result, so charging is the safer accounting default.

The ledger fails closed: a missing, corrupted or unreadable ledger refuses every
release (reason ``privacy_ledger_unavailable``). Only a session created explicitly
with ``BudgetLedger.create_session`` starts at zero spent.
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


class LedgerUnavailable(RuntimeError):
    """The ledger is missing, corrupted or unreadable, so no release may be made."""


def _session_marker(path: Path) -> Path:
    return path.with_name(path.name + ".session")


class BudgetLedger:
    """Cumulative epsilon spend, persisted so a restart cannot reset it.

    Fails closed. Only ``create_session`` (called by the start scripts) starts a
    ledger at zero spent, and it leaves a session marker next to the ledger. A
    missing, corrupted or unreadable ledger raises ``LedgerUnavailable`` and the
    gate refuses every release; a deleted ledger cannot be recreated while its
    session marker exists.
    """

    def __init__(self, path: Path, total: float = TOTAL_BUDGET):
        self.path, self.total = Path(path), total

    @classmethod
    def create_session(cls, path: Path, total: float = TOTAL_BUDGET) -> "BudgetLedger":
        """Start a new session ledger at zero spent; never overwrite or recreate one."""
        path = Path(path)
        if path.exists():
            raise FileExistsError(f"ledger already exists: {path}")
        if _session_marker(path).exists():
            raise LedgerUnavailable("ledger for an existing session is missing; refusing to reset it")
        ledger = cls(path, total)
        ledger._write(0.0)
        _session_marker(path).write_text("created\n", encoding="utf-8")
        return ledger

    def _spent(self) -> float:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            spent, total = float(data["spent"]), float(data["total"])
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise LedgerUnavailable("ledger missing or unreadable") from e
        if not math.isfinite(spent) or spent < 0 or total != self.total:
            raise LedgerUnavailable("ledger contents invalid")
        return spent

    def remaining(self) -> float:
        return max(0.0, self.total - self._spent())

    def charge(self, cost: float) -> float:
        """Add ``cost`` to the spend and return the budget remaining afterwards."""
        spent = self._spent() + cost
        self._write(spent)
        return max(0.0, self.total - spent)

    def _write(self, spent: float) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent)
        with os.fdopen(fd, "w") as f:
            json.dump({"spent": spent, "total": self.total}, f)
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
        Post-noise suppressions are charged before returning suppression. An
        unavailable ledger refuses before any count is computed or released.
        """
        try:
            remaining = self.ledger.remaining()
        except LedgerUnavailable:
            return {"suppressed": True, "reason": "privacy_ledger_unavailable"}
        if gate_on < MIN_CELL:
            return {"suppressed": True, "reason": "below_disclosure_threshold"}
        cost = EPSILON_PER_COUNT * len(raw_counts)
        if remaining < cost:
            return {"suppressed": True, "reason": "privacy_budget_exhausted"}
        noised = {
            k: max(0, round(v + laplace(self.scale, self.rng))) for k, v in raw_counts.items()
        }
        try:
            remaining = self.ledger.charge(cost)
        except LedgerUnavailable:
            return {"suppressed": True, "reason": "privacy_ledger_unavailable"}
        if any(value < MIN_CELL for value in noised.values()):
            return {"suppressed": True, "reason": "below_disclosure_threshold"}
        return {
            "suppressed": False,
            "counts": noised,
            "noise_scale": self.scale,
            "budget_remaining": round(remaining / self.ledger.total, 3),
        }
