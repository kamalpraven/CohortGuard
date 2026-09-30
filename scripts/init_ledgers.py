"""Create or continue the clinic privacy ledgers for one session.

Called by the start scripts before any SuperNode starts. A ledger that does not
exist yet is created at zero spent with a session marker. An existing, readable
ledger is continued unchanged. A ledger whose session marker exists but whose file
is missing or unreadable is an error: the budget is never reset.

    python scripts/init_ledgers.py <ledger-path> [<ledger-path> ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "clinic-agents")]

from clinic_core.privacy import BudgetLedger, LedgerUnavailable  # noqa: E402


def init_ledger(path: Path) -> str:
    """Return "created" or "continued"; raise if the ledger cannot be used."""
    if path.exists():
        BudgetLedger(path).remaining()  # raises LedgerUnavailable when corrupted
        return "continued"
    BudgetLedger.create_session(path)
    return "created"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    for value in argv:
        path = Path(value)
        try:
            state = init_ledger(path)
        except LedgerUnavailable as error:
            print(f"refusing to start: {path}: {error}", file=sys.stderr)
            return 1
        remaining = BudgetLedger(path).remaining()
        print(f"ledger {state}: {path} (remaining {remaining:g})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
