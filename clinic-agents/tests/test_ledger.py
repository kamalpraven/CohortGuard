"""BudgetLedger fails closed: only an explicit new session starts at zero spent."""

import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "clinic-b-agent")]

from clinic_b.clinic import handle_clinic_b  # noqa: E402
from clinic_core.privacy import TOTAL_BUDGET, BudgetLedger, LedgerUnavailable, PrivacyGate  # noqa: E402
from clinic_core.store import load_patients  # noqa: E402

B = load_patients("B", ROOT / "clinic-b-agent" / "clinic_b" / "data")
FEAS = {"template": "feasibility_count", "nct_id": "NCT07060456"}
COHORT = {"template": "outcome_rate_by_cohort", "cohort_field": "medication",
          "cohorts": ["metformin", "basal_insulin"], "outcome": "readmit_30d", "filters": {"diagnosis": "T2D"}}
TINY = COHORT | {"filters": {"age_band": "80-89", "diagnosis": "T1D", "sex": "MALE"}}
INIT_SCRIPT = ROOT.parent / "scripts" / "init_ledgers.py"
UNAVAILABLE = "privacy_ledger_unavailable"


def gate_on(path):
    return PrivacyGate(BudgetLedger(path), random.Random(1))


def assert_refuses_everything(path):
    for request in (FEAS, COHORT, TINY):
        reply = handle_clinic_b(request, B, gate_on(path))
        assert reply["status"] == "suppressed" and reply["reason"] == UNAVAILABLE
        assert not reply.get("results") and reply.get("eligible_n") is None


def test_new_session_starts_at_zero_and_releases(tmp_path):
    ledger = BudgetLedger.create_session(tmp_path / "ledger.json")
    assert ledger.remaining() == TOTAL_BUDGET
    assert handle_clinic_b(FEAS, B, gate_on(ledger.path))["status"] == "ok"
    assert ledger.remaining() == TOTAL_BUDGET - 0.5


def test_ledger_never_created_refuses_all_releases(tmp_path):
    path = tmp_path / "never-created.json"
    assert_refuses_everything(path)
    assert not path.exists()


@pytest.mark.parametrize("content", [
    "{not json",
    "",
    "[]",
    json.dumps({"spent": "lots", "total": TOTAL_BUDGET}),
    json.dumps({"total": TOTAL_BUDGET}),
    json.dumps({"spent": -1.0, "total": TOTAL_BUDGET}),
    '{"spent": NaN, "total": 5.0}',
    json.dumps({"spent": 0.0, "total": 50.0}),
])
def test_corrupted_ledger_refuses_all_releases(tmp_path, content):
    ledger = BudgetLedger.create_session(tmp_path / "ledger.json")
    ledger.path.write_text(content, encoding="utf-8")
    assert_refuses_everything(ledger.path)
    with pytest.raises(LedgerUnavailable):
        ledger.remaining()


def test_unreadable_ledger_refuses_all_releases(tmp_path):
    path = tmp_path / "ledger.json"
    path.mkdir()  # a directory where the ledger file should be cannot be read
    assert_refuses_everything(path)


def test_deleted_ledger_refuses_and_cannot_be_recreated(tmp_path):
    ledger = BudgetLedger.create_session(tmp_path / "ledger.json")
    for _ in range(9):
        handle_clinic_b(FEAS, B, gate_on(ledger.path))
    ledger.path.unlink()
    assert_refuses_everything(ledger.path)
    with pytest.raises(LedgerUnavailable):
        BudgetLedger.create_session(ledger.path)
    assert not ledger.path.exists()


def test_create_session_never_overwrites(tmp_path):
    ledger = BudgetLedger.create_session(tmp_path / "ledger.json")
    ledger.charge(4.5)
    with pytest.raises(FileExistsError):
        BudgetLedger.create_session(ledger.path)
    assert ledger.remaining() == 0.5


def test_ledger_removed_between_check_and_charge_releases_nothing(tmp_path):
    ledger = BudgetLedger.create_session(tmp_path / "ledger.json")

    class VanishingRng(random.Random):
        def random(self):
            ledger.path.unlink(missing_ok=True)
            return 0.5

    reply = handle_clinic_b(COHORT, B, PrivacyGate(BudgetLedger(ledger.path), VanishingRng()))
    assert reply["status"] == "suppressed" and reply["reason"] == UNAVAILABLE and reply["results"] == []


def run_init(*paths):
    return subprocess.run([sys.executable, str(INIT_SCRIPT), *map(str, paths)], capture_output=True, text=True)


def test_init_script_creates_then_continues_without_reset(tmp_path):
    path = tmp_path / "session.json"
    assert run_init(path).returncode == 0
    BudgetLedger(path).charge(2.0)
    result = run_init(path)
    assert result.returncode == 0 and "continued" in result.stdout
    assert BudgetLedger(path).remaining() == TOTAL_BUDGET - 2.0


def test_init_script_refuses_missing_or_corrupted_session_ledger(tmp_path):
    missing = tmp_path / "missing.json"
    assert run_init(missing).returncode == 0
    missing.unlink()
    assert run_init(missing).returncode == 1 and not missing.exists()

    corrupted = tmp_path / "corrupted.json"
    assert run_init(corrupted).returncode == 0
    corrupted.write_text("{oops", encoding="utf-8")
    assert run_init(corrupted).returncode == 1
    assert corrupted.read_text(encoding="utf-8") == "{oops"
