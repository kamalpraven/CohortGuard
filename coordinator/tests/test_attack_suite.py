from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _suite():
    spec = importlib.util.spec_from_file_location("run_clinic_attack_suite", ROOT / "scripts/run_clinic_attack_suite.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


def test_clinic_attack_suite_passes_every_case():
    suite = _suite()
    cases = suite.run_suite(seed=suite.DEFAULT_SEED, trials=400)
    failed = [f"{case.category}: {case.name}: {case.detail}" for case in cases if not case.passed]
    assert failed == []
    summary = suite.summarize(cases)
    assert all(counts["total"] > 0 for counts in summary["per_category"].values())
